# verifier/: offline verifier for honesty.tools signed receipts (mirror)

These files are byte-identical copies of the verifier published at https://www.honesty.tools/verifier/.
The source is the honesty.tools site repo, commit f1f2b50 (merged as d7bd360; `aperture_witness.py` from commit 96aadb3), `Aperture/web/static/verifier/` (plus its `demo_witnesses/`, which the witness selftest reads, and `log/`, the signed genesis checkpoint and its Rekor entry). They are
mirrored here so the `verify` step that CHARTER.md promises ships with the public tools.
`SOURCE.sha256` pins the bytes, and `tests/test_verifier.py` fails if a copy drifts from it.
To update, copy the files from the site again and regenerate `SOURCE.sha256` in the same commit.

| file | what it does | needs |
|---|---|---|
| `aperture_verify.py` | Checks that an Aperture read receipt or calibration certificate is Ed25519-signed by a pinned registry key. `verify FILE`, `fetch ID`, `leaf FILE`, `keys`, `selftest` | `cryptography` |
| `transparency_log.py` | An RFC 6962 Merkle log of receipt leaves, with inclusion and consistency proofs and session-trail roots. `selftest` | stdlib; `cryptography` for the session part of the selftest |
| `aperture_witness.py` | Runs a checker over a signed receipt and emits a witness record (see `WITNESS_SPEC.md`). `selftest` | `cryptography`; `sympy` for the math cell (selftest 67/67 with sympy and `demo_witnesses/`; 56/58 without sympy) |

    pip install cryptography
    cd verifier && python3 aperture_verify.py verify ../tests/fixtures/verifier/read_db8f162c66c3.json

**What a pass means:** the bytes were signed by a pinned key and have not changed since. That is provenance, not truth.

**Not yet covered:**
- the log is not published as signed tree heads;
- there is no external co-signer.

See CHARTER stage 4. The design is in progress: C2SP checkpoints, anchored to Sigstore Rekor v2 or C2SP witness cosignatures.

## Signed checkpoints and the Rekor anchor
`log/` holds the receipt log's genesis checkpoint (22 entries), signed by the receipt-log key
held by Kevin Russell (key and fingerprint: [`../keys/`](../keys/)), and its Sigstore Rekor v2 entry
(`log2025-1`, index 171316570). See [`../ANCHORING.md`](../ANCHORING.md).

    python3 transparency_log.py verify-checkpoint log/checkpoint log/leaves.jsonl
    python3 transparency_log.py verify-anchor log/checkpoint log/rekor-entry-0.json

`verify-anchor` re-checks the Ed25519ph entry signature only if `pycryptodome` is installed, and says so if it isn't; the inclusion proof and Rekor's checkpoint signature need only `cryptography`.
