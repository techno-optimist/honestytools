#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""witness_client.py: ask C2SP tlog-witness witnesses to cosign the Honesty.tools receipt log.

  python3 witness/witness_client.py submit --witness-url URL --witness-vkey VKEY [--expect-size N] [--dry-run]
  python3 witness/witness_client.py show                         # fetch + verify the published checkpoint
  python3 witness/witness_client.py status                       # what we've saved so far

What it does (c2sp.org/tlog-witness, add-checkpoint):
  1. fetches the PUBLISHED checkpoint and leaves from https://www.honesty.tools/verifier/log/
     and verifies the log signature against the pinned vkey and that the leaves reproduce the root;
  2. POSTs  "old <size>\\n" + consistency proof lines + "\\n" + checkpoint  to <witness-url>/add-checkpoint,
     using old size 0 (no proof) unless the state file records what this witness last cosigned;
  3. on 409 Conflict reads the witness's size (text/x.tlog.size) and retries ONCE with an RFC 6962
     consistency proof computed from the published leaves (old == size: same-root resend, no proof;
     witness size > ours: STOP, that's a possible fork or an unpublished head reaching a witness);
  4. verifies every returned cosignature/v1 line against the witness vkey you pinned (unknown keys are
     ignored, a bad signature from the pinned key is an error) and saves the cosigned checkpoint.

Safety rails, on purpose:
  * For the production origin the checkpoint can only come from the public URL. There is no flag to
    feed it a local or unpublished checkpoint (e.g. the size-23 head that isn't published).
  * --expect-size refuses to run if the published size isn't the one you meant to submit.
  * At most 24 add-checkpoint requests per witness per rolling 24h (our Witness Network qpd is 24).
  * Never touches a signing key; it only forwards what the log already signed and published.
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import c2sp  # noqa: E402

QPD = 24
MAX_PROOF_LINES = 63
DEFAULT_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")


class WitnessError(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


# ── log side ────────────────────────────────────────────────────────────────────────────────────────
def load_log(args):
    """Fetch + verify the checkpoint and the leaves that reproduce it. Returns a dict."""
    production = args.log_vkey == c2sp.PRODUCTION_VKEY
    if production and (args.checkpoint_url != c2sp.PUBLISHED_CHECKPOINT_URL or args.leaves_url != c2sp.PUBLISHED_LEAVES_URL):
        raise WitnessError("refusing: for %s the checkpoint and leaves must come from the published URLs %s "
                           "(only the published checkpoint may be sent to witnesses)"
                           % (c2sp.PRODUCTION_ORIGIN, c2sp.PUBLISHED_BASE), 2)
    note = c2sp.fetch(args.checkpoint_url).decode("utf-8")
    cp = c2sp.verify_log_checkpoint(note, args.log_vkey)
    name = c2sp.parse_vkey(args.log_vkey)[0]
    if cp["origin"] != name:
        raise WitnessError("checkpoint origin %r != vkey name %r" % (cp["origin"], name), 2)
    leaves = c2sp.parse_leaves_jsonl(c2sp.fetch(args.leaves_url))
    if len(leaves) < cp["size"]:
        raise WitnessError("published leaves (%d) are fewer than the checkpoint size (%d)" % (len(leaves), cp["size"]), 2)
    leaves = leaves[:cp["size"]]
    if c2sp.merkle_root(leaves) != cp["root"]:
        raise WitnessError("published leaves do NOT reproduce the checkpoint root; not submitting", 2)
    if args.expect_size is not None and cp["size"] != args.expect_size:
        raise WitnessError("published checkpoint has size %d, but --expect-size %d; not submitting"
                           % (cp["size"], args.expect_size), 2)
    cp["leaves"] = leaves
    return cp


def build_body(old: int, proof, note: str) -> bytes:
    if old < 0 or len(proof) > MAX_PROOF_LINES:
        raise WitnessError("bad old size / proof length", 2)
    head = "old %d\n" % old + "".join(c2sp.b64(h) + "\n" for h in proof) + "\n"
    return head.encode() + note.encode("utf-8")


def proof_for(cp, old: int):
    if old > cp["size"]:
        raise WitnessError("old size %d > checkpoint size %d" % (old, cp["size"]), 2)
    if old in (0, cp["size"]):
        return []
    proof = c2sp.consistency_proof(old, cp["leaves"])
    old_root = c2sp.merkle_root(cp["leaves"][:old])
    if not c2sp.verify_consistency(old, cp["size"], proof, old_root, cp["root"]):  # self-check before sending
        raise WitnessError("internal error: our own consistency proof %d->%d does not verify" % (old, cp["size"]), 2)
    return proof


# ── state ───────────────────────────────────────────────────────────────────────────────────────────
def _safe(s):
    return "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in s)


class State:
    def __init__(self, root, origin):
        self.dir = os.path.join(root, _safe(origin))
        os.makedirs(os.path.join(self.dir, "cosigned"), exist_ok=True)
        self.path = os.path.join(self.dir, "witnesses.json")
        self.data = json.load(open(self.path)) if os.path.exists(self.path) else {}

    def witness(self, name):
        return self.data.setdefault(name, {"last_cosigned_size": None, "attempts": []})

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def note_attempt(self, name, now):
        w = self.witness(name)
        w["attempts"] = [t for t in w["attempts"] if t > now - 86400] + [now]
        self.save()

    def recent_attempts(self, name, now):
        return [t for t in self.witness(name)["attempts"] if t > now - 86400]

    def log_event(self, event):
        with open(os.path.join(self.dir, "events.jsonl"), "a") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    def save_cosigned(self, cp, new_lines):
        """Merge cosignature lines into <size>-<root8>.checkpoint (log signature first, de-duplicated)."""
        fn = os.path.join(self.dir, "cosigned", "%d-%s.checkpoint" % (cp["size"], cp["root"].hex()[:16]))
        if os.path.exists(fn):
            body, sigs = c2sp.split_note(open(fn, encoding="utf-8").read())
            assert body == cp["body"], "saved checkpoint body differs"
        else:
            body, sigs = c2sp.split_note(cp["note"])
        for l in new_lines:
            if l not in sigs:
                sigs.append(l)
        tmp = fn + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body + "\n" + "".join(s + "\n" for s in sigs))
        os.replace(tmp, fn)
        return fn


# ── witness side ────────────────────────────────────────────────────────────────────────────────────
def post(url, body, timeout):
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"User-Agent": c2sp.USER_AGENT, "Content-Type": "text/plain; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def add_checkpoint_url(base):
    base = base.rstrip("/")
    return base if base.endswith("/add-checkpoint") else base + "/add-checkpoint"


def explain(status, body):
    txt = body.decode("utf-8", "replace").strip()[:300]
    return {
        400: "400 Bad Request: the witness says the request is malformed (or old size > checkpoint size)",
        403: "403 Forbidden: no signature from a key the witness trusts for our origin verified "
             "(witness has a different vkey for us?)",
        404: "404 Not Found: this witness does not know our origin yet. Witness Network witnesses pull the "
             "testing list on their own schedule (3 min to hourly per their about pages); try later",
        422: "422 Unprocessable Entity: the consistency proof failed, or same size with a different root. "
             "If our proof self-checked, this is evidence of a split view; STOP and investigate",
        429: "429 Too Many Requests: back off",
        503: "503 Service Unavailable: witness down or rate limiting; back off",
    }.get(status, "HTTP %d" % status) + (" | body: %s" % txt if txt else "")


def submit(args):
    cp = load_log(args)
    wname, wtype, _, _ = c2sp.parse_vkey(args.witness_vkey)
    if wtype != c2sp.SIG_COSIGNATURE_V1:
        raise WitnessError("witness vkey must be type 0x04 (cosignature/v1 Ed25519)", 2)
    url = add_checkpoint_url(args.witness_url)
    st = State(args.state_dir, cp["origin"])
    w = st.witness(wname)
    if args.old_size is not None:
        old = args.old_size
    elif w.get("last_cosigned_size") is not None and w["last_cosigned_size"] <= cp["size"]:
        old = w["last_cosigned_size"]
    else:
        old = 0
    proof = proof_for(cp, old)
    body = build_body(old, proof, cp["note"])
    print("log:     %s size %d root %s (log signature verified, leaves reproduce root)"
          % (cp["origin"], cp["size"], c2sp.b64(cp["root"])))
    print("witness: %s  ->  POST %s" % (wname, url))
    if args.dry_run:
        print("---- dry run: request body (%d bytes), NOT sent ----" % len(body))
        sys.stdout.write(body.decode())
        print("---- end ----")
        return 0

    for attempt in (1, 2):
        now = int(time.time())
        if len(st.recent_attempts(wname, now)) >= QPD:
            raise WitnessError("rate guard: already %d add-checkpoint requests to %s in the last 24h (qpd %d)"
                               % (QPD, wname, QPD), 3)
        st.note_attempt(wname, now)
        status, ctype, resp = post(url, body, args.timeout)
        st.log_event({"t": now, "witness": wname, "url": url, "old": old, "size": cp["size"],
                      "root": cp["root"].hex(), "status": status, "resp": resp.decode("utf-8", "replace")[:2000]})
        if status == 200:
            lines = resp.decode("utf-8").split("\n")
            good = c2sp.verify_cosignature_lines(lines, args.witness_vkey, cp["origin"], cp["size"], cp["root"])
            if not good:
                raise WitnessError("200 but no valid cosignature from %s in the response" % wname, 4)
            fn = st.save_cosigned(cp, [l for l, _ in good])
            w["last_cosigned_size"] = cp["size"]
            w["last_cosigned_root"] = cp["root"].hex()
            w["last_cosignature_time"] = good[-1][1]
            w["url"] = url
            st.save()
            for l, ts in good:
                print("  ✓ cosignature verified: %s at %s" % (l[:60] + "…", datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()))
            print("  saved %s" % fn)
            return 0
        if status == 409 and attempt == 1:
            txt = resp.decode("ascii", "replace")
            if "text/x.tlog.size" not in ctype or not txt.endswith("\n") or not txt[:-1].isdigit():
                raise WitnessError("409 without a valid text/x.tlog.size body: %r %r" % (ctype, txt[:80]), 4)
            wsize = int(txt[:-1])
            print("  409 Conflict: witness's latest cosigned size for us is %d (we sent old %d)" % (wsize, old))
            if wsize > cp["size"]:
                raise WitnessError("ALARM: witness has cosigned size %d for our origin, larger than our published "
                                   "checkpoint (%d). Either an unpublished head reached it or someone has our key. "
                                   "Not retrying." % (wsize, cp["size"]), 5)
            old = wsize
            proof = proof_for(cp, old)
            body = build_body(old, proof, cp["note"])
            print("  retrying with old %d and a %d-line RFC 6962 consistency proof" % (old, len(proof)))
            continue
        raise WitnessError(explain(status, resp), 4 if status != 422 else 6)
    raise WitnessError("still conflicting after one retry; witness state is moving under us", 4)


def show(args):
    cp = load_log(args)
    print("✓ %s size %d root %s; log signature verified against %s; %d leaves reproduce the root"
          % (cp["origin"], cp["size"], c2sp.b64(cp["root"]), args.log_vkey, len(cp["leaves"])))
    sys.stdout.write(cp["note"])
    return 0


def status(args):
    origin = c2sp.parse_vkey(args.log_vkey)[0]
    st = State(args.state_dir, origin)
    print(json.dumps(st.data, indent=1, sort_keys=True))
    for f in sorted(os.listdir(os.path.join(st.dir, "cosigned"))):
        print("cosigned/" + f)
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--log-vkey", default=c2sp.PRODUCTION_VKEY, help="pinned log vkey (default: production)")
    common.add_argument("--checkpoint-url", default=c2sp.PUBLISHED_CHECKPOINT_URL)
    common.add_argument("--leaves-url", default=c2sp.PUBLISHED_LEAVES_URL)
    common.add_argument("--state-dir", default=DEFAULT_STATE)
    common.add_argument("--expect-size", type=int, help="refuse unless the published checkpoint has this size")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit", parents=[common])
    s.add_argument("--witness-url", required=True, help="submission prefix, or the full .../add-checkpoint URL")
    s.add_argument("--witness-vkey", required=True, help="witness vkey, type 0x04, from its about page")
    s.add_argument("--old-size", type=int, help="override the old size (default: state file, else 0)")
    s.add_argument("--dry-run", action="store_true", help="print the request, send nothing")
    s.add_argument("--timeout", type=int, default=30)
    sub.add_parser("show", parents=[common])
    sub.add_parser("status", parents=[common])
    args = p.parse_args(argv)
    try:
        return {"submit": submit, "show": show, "status": status}[args.cmd](args)
    except WitnessError as e:
        print("  ✗ %s" % e, file=sys.stderr)
        return e.code
    except ValueError as e:
        print("  ✗ %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
