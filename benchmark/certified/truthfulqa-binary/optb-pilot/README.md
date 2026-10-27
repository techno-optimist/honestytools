# TruthfulQA option B pilot: model rewrites (NEGATIVE RESULT, NOT a certified split)

**This directory is not a certified split. Do not use it as a benchmark.** It is published as a documented negative finding: rewriting TruthfulQA answer pairs with a language model did not make them certifiable. TruthfulQA is final **NOT_CERTIFIABLE** for FLOOR v1.

Run 2026-10-26 23:52 to 2026-10-27 00:01 MDT.

## Licences
- TruthfulQA: Apache-2.0 (the Judging-by-the-Cover 790-pair set, `../source_790.json`).
- Rewriter `microsoft/phi-4`: MIT, not gated. Snapshot `932b33c0ec9ca189badeb22480721a8de9d0e006`.
- Meaning check `microsoft/deberta-large-mnli`: MIT, not gated. Revision `7296194b9009373def4f7c5dad292651e4b5cf4e`.

## What was done
- **Sample:** 100 pairs, stratified random from `source_790.json` (seed 20261028), in `pilot_original.json`.
- **Rewrite** (`rewrite_pilot.py`): phi-4 in bf16 on one GPU. Greedy decoding first, then up to 2 sampled retries (temperature 0.7, top_p 0.95). The prompt shows both answers in seeded random order, so the rewriter never sees which one is correct. It asks for the same claims, a shared sentence template, lengths within 2 words, affirmative phrasing (negation only where the meaning needs it), and no signal of which answer is true. The full prompt is in `pilot_rewritten.audit.json` under `meta`.
- **Meaning check:** NLI P(entailment) ≥ 0.5 in both directions (original to rewrite and rewrite to original), for each answer, with the question as context. The rewritten right answer also must not entail the rewritten wrong answer.
- **Result:** 82 of 100 pairs passed. Failures overlap: 12 right-answer, 5 wrong-answer and 4 collapse (the two answers became the same claim). Per-pair NLI scores and failure reasons are in `pilot_rewritten.audit.json`.
- **Certification:** `certify.py --no-filter --source pilot_original.json` on the 82 rewritten pairs (`rew/`), with the same 82 original pairs as a control (`orig/`; inputs in `pilot_original_kept82.json`).

## Results (82 pairs; both are below the 320 floor, so both are NOT_CERTIFIABLE regardless)

| measure | originals (`orig/`) | rewrites (`rew/`) |
|---|---|---|
| status | NOT_CERTIFIABLE | NOT_CERTIFIABLE |
| paired worst cell A\* | length 0.707 | words 0.701 |
| unpaired worst cell | 0.614 | lexicality 0.757 |
| held-out char n-gram | 0.707 | 0.780 |
| held-out negation | 0.677 | 0.671 |
| held-out hedge | 0.579 (significant) | 0.561 (not significant) |
| negation sign test (right:wrong more negated) | 34:5 | 31:3 |
| mean length difference | 2.04 words | 1.43 words |

## The negative finding
Rewriting narrowed the length gap and weakened the hedge cue. It did not remove the negation cue (31:3) or the character n-gram cue (0.780, higher than the originals). Option A (`../../truthfulqa-binary-negbal/`, balancing negation by selection) also failed (NOT_CERTIFIED). The reason is the same in both: in TruthfulQA, negation is part of the correct answer's content ("No, ...", "Nothing happens", "There is no ..."). A meaning-preserving rewrite has to keep it, so the cue can't be removed without changing what the answers say.
