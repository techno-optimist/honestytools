# witness/: asking Witness Network witnesses to cosign the receipt log

`www.honesty.tools/receipts` is on the Witness Network **testing** list
(<https://testing.witness-network.org/log-list.1>):

```
vkey www.honesty.tools/receipts+fcd79bdd+AUF/J2nQKn90Ba52NjK0jqgIYayocRQGAYsK7PUdD7+C
qpd 24
contact hello@honesty.tools
```

Witnesses do not come and fetch our checkpoint (we publish `leaves.jsonl`, not tiles). The log
**pushes** each new checkpoint to each witness's `add-checkpoint` endpoint and stores the cosignatures
it gets back. This directory holds that client and the append-only tooling.

| file | what it is |
|---|---|
| `witness_client.py` | C2SP tlog-witness client: fetch + verify the published checkpoint, send `old 0` first, handle 409 with an RFC 6962 consistency proof, verify and save cosignatures, `--dry-run` |
| `c2sp.py` | signed-note / tlog-checkpoint / tlog-cosignature parsing and verification (reuses `verifier/transparency_log.py`, which is left untouched) |
| `log_consistency.py` | `archive`, `prove`, `verify`: shows that any later checkpoint only appends to the size-22 genesis |
| `archive/size-22/` | the published genesis checkpoint, leaves and companions, with `SHA256SUMS` (the same bytes as `verifier/log/`) |
| `testlog.py` | throwaway test logs (ephemeral key) for tests |
| `e2e/run_litewitness_e2e.sh` | interop test against a local litewitness; `e2e/testagent` is a tiny in-memory ssh-agent for it |
| `../tests/test_witness_client.py` | CI unit tests against an in-process fake witness built from the spec |

## Safety rails in the client

- **Only the published checkpoint.** For origin `www.honesty.tools/receipts`, the client refuses any checkpoint
  or leaves source other than `https://www.honesty.tools/verifier/log/` (exit 2). It never signs anything and
  has no access to the log key, which stays on the DGX. Unpublished heads cannot be sent by mistake.
- It verifies the log's Ed25519 signature against the pinned vkey and recomputes the root from the published leaves.
  `--expect-size N` refuses to send unless the published size is exactly N.
- Each consistency proof is checked locally before it is sent.
- Each cosignature is verified against the witness vkey you pass in (`cosignature/v1`, type 0x04). Lines from other keys
  are ignored, as the spec requires.
- Rate guard: at most 24 attempts per witness per rolling 24 h, matching our declared `qpd 24` (exit 3).
- 409 with a witness size **larger** than ours means the witness has cosigned a head we never published.
  The client raises an ALARM (exit 5) and does not retry. A 422 means a split view or a failed proof (exit 6). Stop and investigate.
- State lives in `witness/state/<origin>/` (gitignored): `witnesses.json` (last cosigned size/root per witness),
  `events.jsonl` (every attempt), and `cosigned/<size>-<root>.checkpoint` (the checkpoint with all cosignatures collected).

Exit codes: 0 ok · 2 refused locally · 3 rate guard · 4 HTTP/transport error · 5 ALARM · 6 422.

## Usage

```
python3 witness/witness_client.py show                       # fetch + verify the published checkpoint
python3 witness/witness_client.py submit --expect-size 22 \
    --witness-url <add-checkpoint URL or prefix> --witness-vkey '<witness vkey>' --dry-run
python3 witness/witness_client.py status                     # what each witness last cosigned

python3 witness/log_consistency.py prove NEW_CHECKPOINT NEW_LEAVES   # vs the size-22 archive
python3 witness/log_consistency.py verify NEW_CHECKPOINT PROOF.json  # for monitors without leaves
```

## What the specs say (quoted)

- C2SP tlog-witness (<https://github.com/C2SP/C2SP/blob/main/tlog-witness.md>): the request body is `old <size>`, then
  consistency-proof lines, then an empty line, then the checkpoint. *"If a client doesn't have information on the latest
  cosigned checkpoint, it MAY initially make a request with a old size of zero to obtain it."* A 409 body is the
  witness's current size, with `Content-Type: text/x.tlog.size`. 422 is returned when a proof is present with old size 0,
  when the proof fails, or when the size is the same but the root differs.
- C2SP tlog-cosignature (<https://github.com/C2SP/C2SP/blob/main/tlog-cosignature.md>): the signed message is
  `cosignature/v1\ntime <t>\n<checkpoint body>`, and the signature is `keyID(4) || timestamp(8, big-endian) || Ed25519 sig(64)`.
- Witness Network (<https://witness-network.org/participate/>): logs use an Ed25519 key whose name is the origin. Exceeding
  the declared rate *"may cause participating witnesses to rate limit or completely block the log"*. The testing list is
  *"Used for development and shorter-lived testing"* and its page warns that the lists are unstable and may be reset.

## Not done yet

- No automatic cadence. Submitting after each new published checkpoint is a manual step, and it runs wherever the published
  checkpoint can be fetched. The client never needs the DGX key.
- Only Ed25519 `cosignature/v1` witness keys are supported. The spec recommends ML-DSA-44 for new witnesses, and the client will
  refuse such a vkey rather than accept it unverified.
- Cosignatures are kept in local state and are not published yet. Deciding to publish a cosigned checkpoint is a separate step.
