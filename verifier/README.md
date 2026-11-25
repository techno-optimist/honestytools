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
- the log has one signed checkpoint (the 22-leaf genesis, anchored in Sigstore Rekor; see below), not a regularly published series of signed tree heads, and later receipts are not in it;
- that checkpoint is signed by us; there is no external witness co-signer in this mirror.

See CHARTER stage 4: regular C2SP checkpoints and C2SP witness cosignatures are still to do.

**The live log is ahead of this mirror (checked 2026-11-02).** The log served at
https://www.honesty.tools/verifier/log/ has checkpoints 22 and 23 (`log/checkpoints/22`, `log/checkpoints/23`),
each anchored in Sigstore Rekor v2 (`log/rekor-entry-0.json`, `log/rekor-entry-1.json`), a consistency proof
(`log/proofs/consistency-22-23.json`), and checkpoint 23 co-signed by `rgdd.se/poc-witness` and
`markovianprotocol.com/witness` (`log/cosigned/23`, keys in `log/witnesses.txt`). Both witnesses are on the
Witness Network testing list, which may be reset; this is not production witnessing. The served
`transparency_log.py` has a `consistency` command and the site also serves `verify_cosignatures.py`; neither
is mirrored here yet. The commands below check this mirror's size-22 genesis only. To check the live log, fetch
the served `/verifier/` files and follow the served `log/KEYHOLDER.md`.

Read the served `verify_cosignatures.py` output line by line (checked 2026-11-16). It exits 0 when the log
signature verifies and the body matches `log/checkpoints/<size>`; a witness with no valid co-signature is printed
as `- <witness>: no valid co-signature on this checkpoint` and does not change the exit code, so exit 0 alone does
not show that either witness co-signed. It also does not report how old a co-signature is. The newest
co-signatures on the live log are from 2026-10-28, and Markovian's own history for this log
(https://witness.markovianprotocol.com/7b4fdd1b1fe4949d9f3b9423c653206423a7ea00ee63138d4f1c3fb1bef10d7d/history) has that one entry.

The served `verify_cosignatures.py` pins only the log key (checked 2026-11-19). It reads the witness keys from the
`witnesses.txt` next to the file you check, so whatever keys that file lists are the witnesses it reports. With a
made-up witness key and a co-signature from it dated 2030-01-01, it printed a ✓ for that witness and exited 0. It
does not reject co-signature times in the future. Compare each line of `witnesses.txt` with that witness's own
about page (linked in the file) and check that no co-signature time is later than now. On 2026-11-19 both served
keys matched those pages.

The served `transparency_log.py verify-checkpoint` (checked 2026-11-18) differs from this mirror's. This mirror's
requires the leaves file to have exactly the checkpoint's size. The served one compares only the first `<size>`
leaves, so that older checkpoints verify against the newest leaves file: a leaves file with more rows than the
checkpoint still exits 0 and prints `the first <size> of the <n> leaves ... reproduce the checkpoint root`.
Neither reads the `index` field of each row. When you check the live log, compare the row count of
`log/leaves.jsonl` with the size in `log/checkpoint` yourself (23 on 2026-11-18).

Signature lines are decoded leniently (checked 2026-11-25). This mirror's `transparency_log.py verify-checkpoint`,
and the served `transparency_log.py` and `verify_cosignatures.py`, accept a signature line rewritten in
non-canonical base64 (the same bytes with a different last character) or with stray characters such as `#!*`
inserted: each still prints ✓ and exits 0 (served: log line and both witness lines of `log/cosigned/23`; this
mirror: the log line of `log/checkpoint`). The C2SP signed-note spec says decoders must reject non-canonical
encodings. No signature is forged, since the decoded bytes are the same, but two files that differ byte for byte
can both verify, so compare checkpoints by their verified content rather than by a hash of the file.

What the live log covers (checked 2026-11-25). Its 23 leaves are the 20 registry certificates and 2 example read
receipts in `log/leaves_manifest.json`, plus leaf 22, which the site's /goodfire page names as the
goodfire-sdxl-7446 bundle (manifest not published). We found no leaf and no signature for the other pages the
site publishes under `/receipts/` (including the VP ≠ VNP statement reads of 23 and 24 November 2026); their files
are pinned only by the `SHA256SUMS` published with each page and by the site's git history.

## Signed checkpoints and the Rekor anchor
`log/` holds the receipt log's genesis checkpoint (22 entries), signed by the receipt-log key
held by Kevin Russell (key and fingerprint: [`../keys/`](../keys/)), and its Sigstore Rekor v2 entry
(`log2025-1`, index 171316570). See [`../ANCHORING.md`](../ANCHORING.md).

    python3 transparency_log.py verify-checkpoint log/checkpoint log/leaves.jsonl
    python3 transparency_log.py verify-anchor log/checkpoint log/rekor-entry-0.json

`verify-anchor` re-checks the Ed25519ph entry signature only if `pycryptodome` is installed, and says so if it isn't; the inclusion proof and Rekor's checkpoint signature need only `cryptography`.
