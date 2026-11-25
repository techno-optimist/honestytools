# Honesty.tools receipt-log signing key

**Holder:** Kevin Russell, Lead Researcher, Honesty.tools.
**Custody:** generated on 2026-10-27T05:54:21Z (UTC) on Kevin Russell's DGX and kept only there, in a 0700 directory with a 0600 key file. The private key has never been copied off that machine and is not in any repository, log or chat.
**Purpose:** signs C2SP checkpoints (signed tree heads) of the honesty.tools receipt log, and the Rekor v2 hashedrekord entries that anchor them. It is a different key from the registry key that signs receipts and certificates (`/api/cert/pubkey`), so a leak of one does not give the other.

| | |
|---|---|
| Algorithm | Ed25519 (checkpoints: pure Ed25519, C2SP signed-note type 0x01; Rekor: Ed25519ph, `PKIX_ED25519_PH`) |
| Key name / origin | `www.honesty.tools/receipts` |
| Public key (raw, base64) | `QX8nadAqf3QFrnY2MrSOqAhhrKhxFAYBiwrs9R0Pv4I=` |
| Fingerprint, SHA-256 of raw public key | `d1f9d9386e4173ba6e5e80ad3bb624cf18ee389fe9ba8ed6669be195f4a76342` |
| Fingerprint, SHA-256 of SPKI DER | `8b21a4d9bb2b654cd9b288e16c8b3e1f8688041ebcd2355803b4b1887fa11e8c` |
| Signed-note key ID | `fcd79bdd` |
| Verifier key (vkey) | `www.honesty.tools/receipts+fcd79bdd+AUF/J2nQKn90Ba52NjK0jqgIYayocRQGAYsK7PUdD7+C` |

The same vkey is pinned in `transparency_log.py` (`PINNED_LOG_VKEYS`) and published at
`https://www.honesty.tools/verifier/log/receipt-log.vkey` and in the honestytools repo under `keys/`.
Compare all three before trusting a checkpoint.

## Genesis checkpoint (tree size 22)
- Scope: every registry certificate listed at `/api/cert/list` on 2026-10-27 (20) plus the 2 published example read receipts, each signature verified against the pinned registry key before inclusion, ordered by `registered_at` then id. Read receipts kept only in the database are not in this genesis tree. See `leaves_manifest.json`.
- Root (hex): `890d25bd770f265415256a5fda48013a25ff78fc543711e2785376ad05c8ae71`
- Anchored in Sigstore Rekor v2 `log2025-1.rekor.sigstore.dev`, log index **171316570** (hashedrekord 0.0.2, SHA2_512 digest of the checkpoint body). The full TransparencyLogEntry with its inclusion proof and Rekor's signed checkpoint is `rekor-entry-0.json`.

## Checkpoint 23
- Appends one leaf (index 22, leaf `e1d169de56ba81685b4c07e253138219914c4c42b59b963ad08093930fa82579`) to the genesis tree. Leaves 0–21 are unchanged.
- Root (hex): `97d2ecae83e6e9b2c06fb0f87110b277f5e8a216c07658d988222c57b8c5d72b`
- Anchored in Sigstore Rekor v2 `log2025-1.rekor.sigstore.dev`, log index **171750012** (same entry type and key as above). The full TransparencyLogEntry is `rekor-entry-1.json`.
- `proofs/consistency-22-23.json` is the RFC 6962/9162 consistency proof that the size-23 tree only appends to the size-22 tree.

## Layout
- `checkpoints/<size>`: every signed checkpoint, byte-identical to what was signed and anchored, kept forever. `checkpoints/22` is the genesis checkpoint.
- `checkpoint`: a byte-identical copy of the newest file in `checkpoints/` (currently `checkpoints/23`). It moves on each new checkpoint, so pin a size when you verify an anchor.
- `rekor-entry-<k>.json`: the k-th Rekor anchor. Entry 0 anchors `checkpoints/22`, entry 1 anchors `checkpoints/23`.
- `proofs/consistency-<m>-<n>.json`: a consistency proof between consecutive checkpoints.
- `leaves.jsonl`: the leaves of the newest checkpoint. An older checkpoint verifies against its first `<size>` leaves.

Verify offline, from the `verifier/` directory:

    python3 transparency_log.py verify-anchor log/checkpoints/22 log/rekor-entry-0.json
    python3 transparency_log.py verify-anchor log/checkpoints/23 log/rekor-entry-1.json
    python3 transparency_log.py consistency log/checkpoints/22 log/checkpoints/23 log/proofs/consistency-22-23.json
    python3 transparency_log.py verify-checkpoint log/checkpoints/23 log/leaves.jsonl

What ANCHORED proves: this log head existed, signed by this key, no later than Rekor logged it, so we can't later present a different history for the entries under an anchored checkpoint without it being detectable. CONSISTENT proves the later checkpoint only appended to the earlier one. It does not prove any receipt's content is true, and it does not cover receipts issued after the newest anchored checkpoint until the next one is anchored.

Read the `verify-checkpoint` output line by line (checked 2026-11-18). So that older checkpoints verify against the newest leaves file, it compares only the first `<size>` leaves: a leaves file with more rows than the checkpoint still exits 0 and prints `the first <size> of the <n> leaves in the leaves file reproduce the checkpoint root`, and it does not read the `index` field of each row. So exit 0 on the newest checkpoint shows that the file's first rows match that head, not that the file holds nothing unlogged. Check that `log/leaves.jsonl` has exactly as many rows as the size in `log/checkpoint` (23 today).

## Witness co-signatures (published 2026-10-28)
- `cosigned/<size>`: the signed checkpoint `checkpoints/<size>` plus the witness co-signature lines we received for it, exactly as returned (C2SP tlog-cosignature, `cosignature/v1`). The note body is byte-identical to `checkpoints/<size>`.
- `cosigned/22`: co-signed by `rgdd.se/poc-witness` at 2026-10-28 12:21:41 UTC.
- `cosigned/23`: co-signed by `rgdd.se/poc-witness` at 16:00:37 UTC and `markovianprotocol.com/witness` at 16:00:48 UTC, 2026-10-28. Markovian's first look at this log was checkpoint 23 (trust on first use), so only rgdd.se/poc-witness has checked 22 -> 23 itself.
- `witnesses.txt`: both witness keys, each copied from the witness's own about page (linked in the file). Compare them there.
- Both witnesses are on the Witness Network **testing** list, which is meant for testing and may be reset. This is not production witnessing.

Check them offline (needs the `cryptography` package):

    python3 verify_cosignatures.py log/cosigned/23

A co-signature means that witness saw this log head and found it consistent with every earlier head it had signed for this log. Like the anchor, it does not prove any receipt's content is true.

Read the output line by line (checked 2026-11-16). `verify_cosignatures.py` exits 0 when the log signature verifies and the body matches `checkpoints/<size>`. A witness with no valid co-signature is printed as `- <witness>: no valid co-signature on this checkpoint` and does not change the exit code, so exit 0 alone does not show that either witness co-signed. The script also does not report how old a co-signature is. The newest co-signatures on this log are from 2026-10-28, and Markovian's own history for this log (https://witness.markovianprotocol.com/7b4fdd1b1fe4949d9f3b9423c653206423a7ea00ee63138d4f1c3fb1bef10d7d/history) has that one entry.

Check `witnesses.txt` before you trust a ✓ (checked 2026-11-19). The script pins only the log key. It reads the witness keys from the `witnesses.txt` next to the file you check, so whatever keys that file lists are the witnesses it reports. With a made-up witness key in `witnesses.txt` and a co-signature from that key dated 2030-01-01, it printed `✓ witness.example.invalid/made-up co-signed at 2030-01-01 00:00:00 UTC` and exited 0. It does not reject co-signature times in the future (c2sp.org/tlog-cosignature lets a verifier reject them). So compare each line of `witnesses.txt` with that witness's own about page, linked in the file, and check that no co-signature time is later than now. On 2026-11-19 both served keys matched those pages.

What this log covers, and what it does not (checked 2026-11-25). Of the six packs under `/receipts/`, the /goodfire page names one, `goodfire-sdxl-7446`, as in this log: its bundle is leaf 22 (the 23rd, `e1d169de…`), added in checkpoint 23 and anchored by `rekor-entry-1.json`. That bundle's file-list manifest is published at `/receipts/goodfire-sdxl-7446/anchor/BUNDLE_MANIFEST.json` (sha256 `11042e952f05143b69f9da5db093c2f8867dc38d3b7da226a14110169b62d71a`) and indexed as entry 22 in `leaves_manifest.json`; its underlying unredacted bundle files remain private. We found no leaf and no signature for the two VP ≠ VNP statement reads (dated 23 and 24 November 2026) or the two epoch-crosswalk packs, and `goodfire-features2` is not named either way: those pages are called receipts, but their files are pinned only by the `SHA256SUMS` published with each page and by the site's git history. The other 22 leaves are the 20 registry certificates and 2 example read receipts listed in `leaves_manifest.json`.

Strict C2SP signed-note decoding and bounds checks (P85, 2026-11-25). `verify_cosignatures.py` and `transparency_log.py` enforce strict canonical ASCII base64 (`decode_b64_strict`), reject stray characters, whitespace, non-canonical integer sizes, and wrong key (33 B), root (32 B), or signature (68 B log / 76 B witness) byte lengths (`NON-CANONICAL ENCODING`), enforce size-0 root == SHA-256(""), `len(proof) <= 63`, and reject rollback or split-view consistency inputs. Witness-gauntlet negative tests cover these cases in `tests/test_consistency.py` and `Aperture/web/tests/test_receipt_log_cosignatures.py`.

## How often we add checkpoints (stated 2026-11-02)
- So far checkpoints were added by hand, with no schedule. Checkpoints 22 and 23 were both signed and anchored on 2026-10-27 (UTC); 23 was published on 2026-10-28. None since: nothing new has entered the log (the 20 certificates at `/api/cert/list` are the 20 in checkpoint 22, checked 2026-11-02).
- From now on: when a receipt enters the log, we sign, anchor and publish a new checkpoint within 7 days. If nothing enters the log there is no new checkpoint, and https://www.honesty.tools/evidence#checkpoint-cadence gets a new "last checked" date at least once a month.
- This does not prove that nothing was left out. A log we run ourselves can't show that; a schedule only makes a pause visible. Read receipts kept only in our database are not in the log. See draft-chueayen-attestation-receipts-05 and draft-hood-agtp-log-03 for the same point about operator-run logs.

## If the key is lost or compromised
A replacement key will be announced here and in the honestytools repo, signed off by the holder, and old checkpoints stay verifiable under this key. Any checkpoint under this key that conflicts with an anchored one (same size, different root, or failed consistency) is evidence of misuse.
