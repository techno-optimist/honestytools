# Anchoring the honesty.tools receipt log

The receipt log is an RFC 6962 Merkle tree over receipt and certificate leaves (`verifier/transparency_log.py`).
A Merkle root on its own only proves internal consistency: whoever runs the log could quietly rebuild a different tree.
Anchoring closes that gap. We sign a checkpoint (a signed tree head) with a key we publish, and log that checkpoint in a public, append-only log that we don't run.

## The key
- **Holder:** Kevin Russell, Lead Researcher, Honesty.tools.
- **Custody:** the key was generated on his DGX, and the private key stays only there (0700 directory, 0600 file). It is never copied off the machine, printed, or committed.
- **Public key, fingerprint and statement:** [`keys/`](keys/). The same key is published at https://www.honesty.tools/verifier/log/KEYHOLDER.md and pinned in `verifier/transparency_log.py` (`PINNED_LOG_VKEYS`).
- **Fingerprint** (SHA-256 of the raw Ed25519 public key): `d1f9d9386e4173ba6e5e80ad3bb624cf18ee389fe9ba8ed6669be195f4a76342`.
- **Separate from the registry key:** this is not the registry key that signs receipts and certificates.

## The format
- **Checkpoints** follow C2SP [tlog-checkpoint](https://c2sp.org/tlog-checkpoint) and [signed-note](https://c2sp.org/signed-note).
  - Origin: `www.honesty.tools/receipts`.
  - Signature: pure Ed25519, note key ID `fcd79bdd`.
- **Anchor:** a Sigstore Rekor v2 `hashedrekord` 0.0.2 entry on `log2025-1.rekor.sigstore.dev`.
  - The artifact is the checkpoint body.
  - The digest is SHA2_512.
  - The signature is Ed25519ph by the same key (`PKIX_ED25519_PH`), which is domain-separated from the note signature.
- **Rekor key:** pinned from the Sigstore TUF `trusted_root.json`.

## Genesis checkpoint
| | |
|---|---|
| Tree size | 22 |
| Root (hex) | `890d25bd770f265415256a5fda48013a25ff78fc543711e2785376ad05c8ae71` |
| Signed | 2026-10-27T05:55:05Z |
| Rekor | `log2025-1`, log index **171316570**. The inclusion proof is against Rekor tree size 171316599. Rekor v2 returns no UUID, so the log index is the identifier. |
| Files | `verifier/log/checkpoint`, `leaves.jsonl`, `leaves_manifest.json`, `rekor-entry-0.json` |

**Scope.** The genesis tree contains:
- the 20 registry certificates listed at `/api/cert/list` on 2026-10-27;
- the 2 published example read receipts.

Each signature was verified against the pinned registry key before inclusion. Leaves are ordered by `registered_at`, then id.

Read receipts that exist only in the service database can't be listed publicly, so they are **not** in this tree. Later checkpoints will extend the log and must be consistency-proven against this one.

## Verify
```bash
cd verifier
python3 transparency_log.py keys
python3 transparency_log.py verify-checkpoint log/checkpoint log/leaves.jsonl
python3 transparency_log.py verify-anchor log/checkpoint log/rekor-entry-0.json   # pip install cryptography [pycryptodome]
```

`verify-anchor` checks four things:
- the checkpoint signature under the pinned log key;
- that the Rekor entry's digest is SHA-512 of this checkpoint body;
- that the entry's public key is the pinned log key;
- Rekor's own checkpoint signature under the pinned Rekor key, and the RFC 6962 inclusion proof.

The Ed25519ph entry signature is re-checked only when `pycryptodome` is installed. The output says which case applies.
Rekor's checkpoint lists witness cosignatures. They are reported but not verified.

## What this proves and what it doesn't
**It proves** that this exact log head, signed by this key, existed no later than the moment Rekor logged it. A different history for those 22 entries would be detectable.

**It does not prove:**
- that any receipt's content is true;
- anything about receipts issued after the checkpoint, until a newer checkpoint is anchored;
- that the key holder behaved honestly. It only shows that any equivocation would leave evidence.

**Not done:**
- no RFC 3161 timestamp;
- no Witness Network enrolment;
- no automated re-anchoring schedule.
