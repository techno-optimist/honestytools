#!/usr/bin/env python3
"""FLOOR Phase 1b option B pilot: rewrite TruthfulQA pairs with an open-weights model, then check
meaning preservation with a different model (NLI both ways). Runs on the DGX.

The rewriter never sees which answer is correct: the two answers are shown as "Answer 1" and
"Answer 2" in a seeded random order, so it cannot systematically mark the right one.
"""
import json, os, random, re, sys, time, glob
import pandas as pd, torch
from transformers import AutoTokenizer, AutoModelForCausalLM, AutoModelForSequenceClassification

HUB = os.path.expanduser("~/.cache/huggingface/hub")
REWRITER = "microsoft/phi-4"
NLI = "microsoft/deberta-large-mnli"
SEED = 20261028
NEG_WORDS = "not, no, never, nothing, none, nobody, cannot, without, false, myth, untrue, n't"

PROMPT = """You are editing a pair of short answers to the same quiz question, for a benchmark that must not be solvable from wording alone.

Question: {q}
Answer 1: {a1}
Answer 2: {a2}

Rewrite BOTH answers so that:
1. Each rewritten answer makes exactly the same claim as its original. Do not add, drop or change any fact, and do not make an answer more or less certain.
2. The two rewritten answers use the same sentence template (same structure and opening words where possible), differing only where their claims differ.
3. Their lengths differ by at most 2 words.
4. Both are phrased as direct affirmative statements. Avoid negation words ({neg}) unless the meaning truly requires one; if one answer needs a negation, give the other the same number of negation words only if that keeps its meaning exactly.
5. Do not judge which answer is true, and do not signal it.

Reply with only a JSON object: {{"answer_1": "...", "answer_2": "..."}}"""


def snap(repo):
    return sorted(glob.glob(f"{HUB}/models--{repo.replace('/', '--')}/snapshots/*"))[0]


def questions():
    p = glob.glob(f"{HUB}/datasets--truthful_qa/snapshots/*/generation/validation-*.parquet")[0]
    df = pd.read_parquet(p)
    m = {}
    for _, r in df.iterrows():
        for a in [r["best_answer"], *r["correct_answers"], *r["incorrect_answers"]]:
            m.setdefault(("R" if a in set([r["best_answer"], *r["correct_answers"]]) else "W", a.strip()), set()).add(r["question"])
    return m, p


def find_q(qm, right, wrong):
    a = qm.get(("R", right.strip()), set()) & qm.get(("W", wrong.strip()), set())
    return sorted(a)[0] if a else None


def words(t):
    return len(re.findall(r"\S+", t))


def main(inp, out):
    rows = json.load(open(inp))
    qm, qpath = questions()
    rng = random.Random(SEED)
    tp = snap(REWRITER)
    tok = AutoTokenizer.from_pretrained(tp)
    tok.padding_side = "left"
    model = AutoModelForCausalLM.from_pretrained(tp, dtype=torch.bfloat16, device_map="cuda")
    model.eval()
    jobs = []
    for i, r in enumerate(rows):
        q = find_q(qm, r["right"], r["wrong"])
        flip = rng.random() < 0.5
        a1, a2 = (r["wrong"], r["right"]) if flip else (r["right"], r["wrong"])
        jobs.append({"i": i, "q": q, "flip": flip, "a1": a1, "a2": a2})
    res = {}
    t0 = time.time()
    for attempt in range(3):
        todo = [j for j in jobs if j["i"] not in res]
        if not todo:
            break
        for b in range(0, len(todo), 16):
            batch = todo[b:b + 16]
            texts = [tok.apply_chat_template([{"role": "user", "content": PROMPT.format(q=j["q"] or "(question not recovered)", a1=j["a1"], a2=j["a2"], neg=NEG_WORDS)}],
                                             tokenize=False, add_generation_prompt=True) for j in batch]
            enc = tok(texts, return_tensors="pt", padding=True).to("cuda")
            kw = dict(max_new_tokens=160, do_sample=attempt > 0, pad_token_id=tok.eos_token_id)
            if attempt > 0:
                kw.update(temperature=0.7, top_p=0.95)
            with torch.no_grad():
                g = model.generate(**enc, **kw)
            for j, seq in zip(batch, g[:, enc["input_ids"].shape[1]:]):
                s = tok.decode(seq, skip_special_tokens=True)
                mm = re.search(r"\{.*\}", s, re.S)
                try:
                    o = json.loads(mm.group(0))
                    n1, n2 = o["answer_1"].strip(), o["answer_2"].strip()
                except Exception:
                    continue
                if n1 and n2 and (abs(words(n1) - words(n2)) <= 2 or attempt == 2):
                    res[j["i"]] = {"n1": n1, "n2": n2, "attempt": attempt, "raw": s}
            print(f"attempt {attempt} batch {b} done {len(res)}/{len(jobs)} {time.time()-t0:.0f}s", flush=True)
    del model
    torch.cuda.empty_cache()
    # meaning check with a different model
    npth = snap(NLI)
    nt = AutoTokenizer.from_pretrained(npth)
    nm = AutoModelForSequenceClassification.from_pretrained(npth).to("cuda").eval()
    ent = [k for k, v in nm.config.id2label.items() if v.upper() == "ENTAILMENT"][0]

    def p_ent(prem, hyp):
        e = nt(prem, hyp, return_tensors="pt", truncation=True).to("cuda")
        with torch.no_grad():
            return float(torch.softmax(nm(**e).logits[0], -1)[ent])

    out_rows, audit = [], []
    for j in jobs:
        r = rows[j["i"]]
        rec = {"i": j["i"], "src_index": r.get("_src_index"), "cat": r["cat"], "question": j["q"], "orig_right": r["right"], "orig_wrong": r["wrong"]}
        if j["i"] not in res:
            rec.update(ok=False, fail="no_valid_rewrite")
            audit.append(rec)
            continue
        x = res[j["i"]]
        nr, nw = (x["n2"], x["n1"]) if j["flip"] else (x["n1"], x["n2"])
        ctx = (j["q"] + " ") if j["q"] else ""
        sc = {}
        for k, o, n in (("right", r["right"], nr), ("wrong", r["wrong"], nw)):
            sc[k + "_fwd"] = round(p_ent(ctx + o, ctx + n), 4)
            sc[k + "_bwd"] = round(p_ent(ctx + n, ctx + o), 4)
        sc["new_right_entails_new_wrong"] = round(p_ent(ctx + nr, ctx + nw), 4)
        fails = [k for k in ("right_fwd", "right_bwd", "wrong_fwd", "wrong_bwd") if sc[k] < 0.5]
        if sc["new_right_entails_new_wrong"] >= 0.5:
            fails.append("rewrites_collapse")
        rec.update(new_right=nr, new_wrong=nw, nli=sc, attempt=x["attempt"], ok=not fails, fail=",".join(fails) or None,
                   len_diff_words=abs(words(nr) - words(nw)))
        audit.append(rec)
        if not fails:
            out_rows.append({"right": nr, "wrong": nw, "cat": r["cat"], "_src_index": r.get("_src_index")})
    meta = {"rewriter": {"repo": REWRITER, "revision": os.path.basename(tp)},
            "nli": {"repo": NLI, "revision": os.path.basename(npth), "threshold": 0.5,
                    "rule": "P(entailment) >= 0.5 for original->rewrite AND rewrite->original, for both answers, with the question as shared context; and the rewritten right answer must not entail the rewritten wrong one"},
            "truthfulqa_questions": qpath.split("/hub/")[1], "seed": SEED, "prompt": PROMPT,
            "n_in": len(rows), "n_rewritten": len(res), "n_pass": len(out_rows),
            "questions_recovered": sum(1 for j in jobs if j["q"]), "seconds": round(time.time() - t0)}
    json.dump(out_rows, open(out, "w"), ensure_ascii=False, indent=0)
    json.dump({"meta": meta, "pairs": audit}, open(os.path.splitext(out)[0] + ".audit.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: meta[k] for k in ("n_in", "n_rewritten", "n_pass", "questions_recovered", "seconds")}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
