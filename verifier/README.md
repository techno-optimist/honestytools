# verifier/: offline verifier for honesty.tools signed receipts (mirror)

These files are byte-identical copies of the verifier published at https://www.honesty.tools/verifier/
(`Aperture/web/static/verifier/` in the `honesty.tools-site` repository, including `demo_witnesses/`,
`log/`, `verify_cosignatures.py`, and `tests/test_consistency.py`). They are mirrored here so the
`verify` step that `CHARTER.md` promises ships with the public tools.
`SOURCE.sha256` pins the bytes, and `tests/test_verifier.py` fails if a copy drifts from it.
To update, copy the files from the site again and regenerate `SOURCE.sha256` in the same commit.

| file | what it does | needs |
|---|---|---|
| `aperture_verify.py` | Checks that an Aperture read receipt or calibration certificate is Ed25519-signed by a pinned registry key. `verify FILE`, `fetch ID`, `leaf FILE`, `keys`, `selftest` | `cryptography` |
| `transparency_log.py` | An RFC 6962 / 9162 Merkle log of receipt leaves, with inclusion and consistency proofs, C2SP checkpoint/Rekor verification, strict canonical base64 decoding, and session-trail roots. `selftest` | stdlib; `cryptography` for checkpoints and session trails |
| `verify_cosignatures.py` | Checks C2SP signed-note and `cosignature/v1` witness co-signatures offline against `log/witnesses.txt` and `log/checkpoints/<size>`, with strict canonical base64 and length checks. | `cryptography` |
| `aperture_witness.py` | Runs a checker over a signed receipt and emits a witness record (see `WITNESS_SPEC.md`). `selftest` | `cryptography`; `sympy` for the math cell (selftest 67/67 with sympy and `demo_witnesses/`; 56/58 without sympy) |

    pip install cryptography
    cd verifier && python3 aperture_verify.py verify ../tests/fixtures/verifier/read_db8f162c66c3.json

**What a pass means:** the bytes were signed by a pinned key and have not changed since. That is provenance, not truth.

**What a pass does not bind (tool-26 rotation, 2026-11-27):** the signature covers the receipt minus `id` and the `signature` block. On all 23 receipts we tested (the 22 logged ones and the `/api/read/f0c39b1d0a5b` demo), a copy with a different `id`, an extra field inside `signature`, no `alg`, or a `sig` string with a stray character, a newline or a non-canonical last character still prints `✓ VERIFIED … unaltered` (a wrong `id` on a read receipt is labelled `not a read-id salt`); `aperture_verify.py` does not yet use the strict decoder P85 added to the log verifiers. The log leaf hashes the `signature` block, so each of those copies except the `id` change has a leaf that is not in `log/leaves.jsonl`: check a receipt against the log with the bytes from its `source` in `log/leaves_manifest.json`. The demo receipt is not a log leaf. `verify_widget.js` (served, but no page loads it) pins one of the two keys and, unlike this file, rejects a stray character and a re-spelled number literal (`0.9061` written `0.90610`).

## Signed checkpoints, Rekor anchors, and witness co-signatures (P85, 2026-11-25)
`log/` holds both signed checkpoints:
- `log/checkpoints/22`: the 22-leaf genesis checkpoint (20 registry certificates and 2 example read receipts), anchored in Sigstore Rekor v2 (`log2025-1`, index 171316570, `log/rekor-entry-0.json`).
- `log/checkpoints/23` (and `log/checkpoint`): appends leaf 22 (`goodfire-sdxl-7446`, listed in `log/leaves_manifest.json` with its published file-list manifest at https://www.honesty.tools/receipts/goodfire-sdxl-7446/anchor/BUNDLE_MANIFEST.json), anchored in Rekor v2 (index 171750012, `log/rekor-entry-1.json`) and linked to checkpoint 22 by `log/proofs/consistency-22-23.json`.
- `log/cosigned/22` and `log/cosigned/23`: witness co-signatures from `rgdd.se/poc-witness` and `markovianprotocol.com/witness` (`log/witnesses.txt`; both are on the Witness Network testing list, which may be reset).

Verify offline from `verifier/`:

    python3 transparency_log.py verify-anchor log/checkpoints/22 log/rekor-entry-0.json
    python3 transparency_log.py verify-anchor log/checkpoints/23 log/rekor-entry-1.json
    python3 transparency_log.py consistency log/checkpoints/22 log/checkpoints/23 log/proofs/consistency-22-23.json
    python3 transparency_log.py verify-checkpoint log/checkpoints/23 log/leaves.jsonl
    python3 verify_cosignatures.py log/cosigned/23
    python3 tests/test_consistency.py

Both `transparency_log.py` and `verify_cosignatures.py` reject non-canonical base64, stray characters,
whitespace, non-canonical integer sizes, wrong key/root/signature byte lengths (`NON-CANONICAL ENCODING`),
size-0 root != SHA-256(""), `len(proof) > 63`, and rollback or split-view consistency inputs (P85,
2026-11-25). Read `log/KEYHOLDER.md` for scope and witness limits (including that `verify_cosignatures.py`
reads witness keys from `log/witnesses.txt` and prints `- <witness>: no valid co-signature` when a listed
witness is absent, and that the VP ≠ VNP / epoch-crosswalk pages under `/receipts/` are hash-pinned by
`SHA256SUMS` outside this log).
