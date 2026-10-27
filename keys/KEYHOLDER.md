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

Verify offline:

    python3 transparency_log.py verify-checkpoint log/checkpoint log/leaves.jsonl
    python3 transparency_log.py verify-anchor log/checkpoint log/rekor-entry-0.json

What ANCHORED proves: this log head existed, signed by this key, no later than Rekor logged it, so we can't later present a different history for these 22 entries without it being detectable. It does not prove any receipt's content is true, and it does not cover receipts issued after this checkpoint until the next one is anchored.

## If the key is lost or compromised
A replacement key will be announced here and in the honestytools repo, signed off by the holder, and old checkpoints stay verifiable under this key. Any checkpoint under this key that conflicts with an anchored one (same size, different root, or failed consistency) is evidence of misuse.
