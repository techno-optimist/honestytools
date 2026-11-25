#!/usr/bin/env python3
"""Tests for the `consistency` subcommand and the per-size log layout (log/checkpoints/<size>).

Run from the verifier directory:   python3 tests/test_consistency.py
Offline; uses only the published files under log/ plus synthetic trees. Needs `cryptography`."""
import base64
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import transparency_log as tl  # noqa: E402

LOG = os.path.join(HERE, "log")
ROOT22 = "890d25bd770f265415256a5fda48013a25ff78fc543711e2785376ad05c8ae71"
ROOT23 = "97d2ecae83e6e9b2c06fb0f87110b277f5e8a216c07658d988222c57b8c5d72b"


def _read(*p):
    with open(os.path.join(LOG, *p), encoding="utf-8") as f:
        return f.read()


def _ref_verify(m, n, proof, r1, r2):
    """Independent RFC 9162 §2.1.4.2 verifier, written from the RFC text (not shared with transparency_log)."""
    h = lambda a, b: hashlib.sha256(b"\x01" + a + b).digest()
    if m == n:
        return not proof and r1 == r2
    if m == 0 or m > n:
        return False
    if (m & (m - 1)) == 0:
        proof = [r1] + list(proof)
    if not proof:
        return False
    fn, sn = m - 1, n - 1
    while fn & 1:
        fn >>= 1; sn >>= 1
    fr = sr = proof[0]
    for c in proof[1:]:
        if sn == 0:
            return False
        if (fn & 1) or fn == sn:
            fr, sr = h(c, fr), h(c, sr)
            if not (fn & 1):
                while not (fn & 1) and fn != 0:
                    fn >>= 1; sn >>= 1
        else:
            sr = h(sr, c)
        fn >>= 1; sn >>= 1
    return fr == r1 and sr == r2 and sn == 0


def _cli(*args):
    r = subprocess.run([sys.executable, os.path.join(HERE, "transparency_log.py")] + list(args),
                       cwd=HERE, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class SyntheticTrees(unittest.TestCase):
    def test_every_pair_up_to_48(self):
        leaves = [hashlib.sha256(b"leaf-%d" % i).digest() for i in range(48)]
        roots = [None] + [tl.merkle_root(leaves[:k]) for k in range(1, 49)]
        for n in range(1, 49):
            for m in range(1, n + 1):
                p = tl.consistency_proof(m, leaves[:n])
                self.assertTrue(tl.verify_consistency(m, n, p, roots[m], roots[n]), (m, n))
                self.assertTrue(_ref_verify(m, n, p, roots[m], roots[n]), (m, n))
                if m == n:
                    continue
                for i in range(len(p)):                      # any single tampered hash fails
                    q = list(p); q[i] = bytes([q[i][0] ^ 1]) + q[i][1:]
                    self.assertFalse(tl.verify_consistency(m, n, q, roots[m], roots[n]), (m, n, i))
                if p:                                         # truncated / extended proofs fail
                    self.assertFalse(tl.verify_consistency(m, n, p[:-1], roots[m], roots[n]), (m, n, "trunc"))
                self.assertFalse(tl.verify_consistency(m, n, p + [roots[1]], roots[m], roots[n]), (m, n, "ext"))
                self.assertFalse(tl.verify_consistency(m, n, p, roots[n], roots[m]), (m, n, "swap"))

    def test_empty_proof_rejected_not_crash(self):
        r = tl.merkle_root([b"a" * 32] * 3)
        self.assertFalse(tl.verify_consistency(3, 5, [], r, r))


class PublishedLog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c22, cls.c23 = _read("checkpoints", "22"), _read("checkpoints", "23")
        cls.proof = json.loads(_read("proofs", "consistency-22-23.json"))

    def test_checkpoints_roots(self):
        self.assertEqual(tl.verify_checkpoint(self.c22)["root"], ROOT22)
        self.assertEqual(tl.verify_checkpoint(self.c23)["root"], ROOT23)
        with open(os.path.join(LOG, "checkpoint"), "rb") as a, open(os.path.join(LOG, "checkpoints", "23"), "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_published_proof_verifies(self):
        rep = tl.verify_consistency_file(self.c22, self.c23, self.proof)
        self.assertEqual((rep["old_root"], rep["new_root"]), (ROOT22, ROOT23))
        p = [bytes.fromhex(x) for x in self.proof["proof"]]
        self.assertTrue(_ref_verify(22, 23, p, bytes.fromhex(ROOT22), bytes.fromhex(ROOT23)))

    def test_proof_regenerates_byte_identical_from_leaves(self):
        obj = tl.make_consistency_proof(self.c22, self.c23, tl.TransparencyLog(os.path.join(LOG, "leaves.jsonl")).leaves)
        self.assertEqual(json.dumps(obj, indent=2) + "\n", _read("proofs", "consistency-22-23.json"))

    def test_tampered_proof_fails(self):
        for i in range(len(self.proof["proof"])):
            bad = copy.deepcopy(self.proof)
            h = bytearray(bytes.fromhex(bad["proof"][i])); h[-1] ^= 0x01; bad["proof"][i] = h.hex()
            with self.assertRaises(ValueError):
                tl.verify_consistency_file(self.c22, self.c23, bad)

    def test_tampered_proof_fails_on_cli(self):
        import tempfile
        bad = copy.deepcopy(self.proof)
        bad["proof"][1] = "00" * 32
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(bad, f)
        try:
            rc, out = _cli("consistency", "log/checkpoints/22", "log/checkpoints/23", f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(rc, 1, out)
        self.assertIn("NOT CONSISTENT", out)

    def test_dropped_reordered_extra_hash_fail(self):
        for mut in (lambda p: p[:-1], lambda p: p[::-1], lambda p: p + [p[0]], lambda p: []):
            bad = copy.deepcopy(self.proof); bad["proof"] = mut(bad["proof"])
            with self.assertRaises(ValueError):
                tl.verify_consistency_file(self.c22, self.c23, bad)

    def test_header_mismatch_fails(self):
        for k, v in (("old_root", ROOT23), ("new_root", ROOT22), ("old_size", 21), ("new_size", 24),
                     ("origin", "example.test/log"), ("type", "other")):
            bad = copy.deepcopy(self.proof); bad[k] = v
            with self.assertRaises(ValueError):
                tl.verify_consistency_file(self.c22, self.c23, bad)

    def test_swapped_checkpoints_fail(self):
        with self.assertRaises(ValueError):
            tl.verify_consistency_file(self.c23, self.c22, self.proof)

    def test_forged_checkpoint_signature_fails(self):
        text, sig = self.c23.split("\n\n", 1)
        parts = sig.strip().split(" ")
        blob = bytearray(base64.b64decode(parts[2])); blob[10] ^= 1
        forged = text + "\n\n" + " ".join(parts[:2] + [base64.b64encode(bytes(blob)).decode()]) + "\n"
        with self.assertRaises(ValueError):
            tl.verify_consistency_file(self.c22, forged, self.proof)

    def test_anchors_pair_with_their_own_checkpoint_only(self):
        e0 = json.loads(_read("rekor-entry-0.json")); e1 = json.loads(_read("rekor-entry-1.json"))
        self.assertTrue(tl.verify_rekor_anchor(self.c22, e0)["ok"])
        self.assertTrue(tl.verify_rekor_anchor(self.c23, e1)["ok"])
        for note, ent in ((self.c22, e1), (self.c23, e0)):
            with self.assertRaises(ValueError):
                tl.verify_rekor_anchor(note, ent)

    def test_documented_commands(self):
        for args in (["verify-anchor", "log/checkpoints/22", "log/rekor-entry-0.json"],
                     ["verify-anchor", "log/checkpoints/23", "log/rekor-entry-1.json"],
                     ["consistency", "log/checkpoints/22", "log/checkpoints/23", "log/proofs/consistency-22-23.json"],
                     ["verify-checkpoint", "log/checkpoints/22", "log/leaves.jsonl"],
                     ["verify-checkpoint", "log/checkpoints/23", "log/leaves.jsonl"],
                     ["verify-checkpoint", "log/checkpoint", "log/leaves.jsonl"]):
            rc, out = _cli(*args)
            self.assertEqual(rc, 0, (args, out))
        rc, out = _cli("verify-anchor", "log/checkpoints/23", "log/rekor-entry-0.json")
        self.assertEqual(rc, 1, out)



class WitnessGauntletAndStrictEncoding(unittest.TestCase):
    """Planted negative tests modelled on the MarkovianProtocol witness-gauntlet suite + strict C2SP decoding."""

    @staticmethod
    def _non_canonical_last_char(b64_str):
        # Flip an unused low bit in the final data character before "=" or "==".
        alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        pad = len(b64_str) - len(b64_str.rstrip("="))
        assert pad in (1, 2), b64_str
        idx = len(b64_str) - pad - 1
        ch = b64_str[idx]
        pos = alpha.index(ch)
        alt = alpha[pos ^ 1]
        return b64_str[:idx] + alt + b64_str[idx + 1:]

    @classmethod
    def _signed_note(cls, origin, size, root_bytes):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        sk = Ed25519PrivateKey.generate()
        pub = sk.public_key().public_bytes_raw()
        kid = hashlib.sha256(origin.encode() + b"\n\x01" + pub).digest()[:4]
        vk_b64 = base64.b64encode(b"\x01" + pub).decode()
        vk = f"{origin}+{kid.hex()}+{vk_b64}"
        body = tl.checkpoint_body(origin, size, root_bytes)
        sig = kid + sk.sign(body)
        note = (body + b"\n" + (f"\u2014 {origin} {base64.b64encode(sig).decode()}\n").encode()).decode()
        return note, {vk: "test"}

    def test_non_canonical_base64_and_stray_chars_rejected(self):
        c23 = _read("cosigned", "23")
        body, sigs = c23.split("\n\n", 1)
        sig_lines = sigs.splitlines()
        self.assertEqual(len(sig_lines), 3)
        for idx in range(3):
            parts = sig_lines[idx].split(" ")
            # 1) Non-canonical padding bits in base64
            mut_nc = list(sig_lines)
            mut_nc[idx] = " ".join(parts[:2] + [self._non_canonical_last_char(parts[2])])
            with self.assertRaises(ValueError, msg=f"non-canonical sig line {idx}"):
                tl.verify_checkpoint(body + "\n\n" + "\n".join(mut_nc) + "\n")
            # 2) Stray characters inserted into base64
            mut_stray = list(sig_lines)
            mut_stray[idx] = " ".join(parts[:2] + ["#!" + parts[2][:8] + "*" + parts[2][8:]])
            with self.assertRaises(ValueError, msg=f"stray chars sig line {idx}"):
                tl.verify_checkpoint(body + "\n\n" + "\n".join(mut_stray) + "\n")
            # 3) Internal whitespace or missing padding
            mut_ws = list(sig_lines)
            mut_ws[idx] = " ".join(parts[:2] + [parts[2][:10] + " " + parts[2][10:]])
            with self.assertRaises(ValueError, msg=f"whitespace sig line {idx}"):
                tl.verify_checkpoint(body + "\n\n" + "\n".join(mut_ws) + "\n")

        # Root line non-canonical / stray chars
        blines = body.splitlines()
        for bad_root in (self._non_canonical_last_char(blines[2]), blines[2].rstrip("="), blines[2] + "!"):
            bad_note = "\n".join([blines[0], blines[1], bad_root]) + "\n\n" + sigs
            with self.assertRaises(ValueError):
                tl.verify_checkpoint(bad_note)

    def test_wrong_signature_key_and_root_lengths_rejected(self):
        c23 = _read("checkpoints", "23")
        body, sig = c23.split("\n\n", 1)
        parts = sig.strip().split(" ")
        raw = base64.b64decode(parts[2])
        for bad_blob in (raw[:-1], raw + b"\x00", raw[:4] + b"x" * 72):  # 67B, 69B, 76B on a log key
            bad_note = body + "\n\n" + f"{parts[0]} {parts[1]} {base64.b64encode(bad_blob).decode()}\n"
            with self.assertRaises(ValueError):
                tl.verify_checkpoint(bad_note)

        # Wrong vkey length (32B, 34B)
        vk = next(iter(tl.PINNED_LOG_VKEYS))
        name, kid, b64 = vk.split("+", 2)
        vraw = base64.b64decode(b64)
        for bad_vraw in (vraw[:-1], vraw + b"\x00"):
            with self.assertRaises(ValueError):
                tl.parse_vkey(f"{name}+{kid}+{base64.b64encode(bad_vraw).decode()}")

        # Wrong root length (31B, 33B)
        for bad_root in (b"x" * 31, b"y" * 33):
            note, vks = self._signed_note("example.test/len", 1, bad_root)
            with self.assertRaises(ValueError):
                tl.verify_checkpoint(note, vks)

    def test_gauntlet_size0_root_and_canonical_size_and_proof_bounds(self):
        empty_root = hashlib.sha256(b"").digest()
        ok0, vks = self._signed_note("example.test/zero", 0, empty_root)
        self.assertEqual(tl.verify_checkpoint(ok0, vks)["tree_size"], 0)
        bad0, vks_bad = self._signed_note("example.test/zero", 0, b"\x00" * 32)
        with self.assertRaises(ValueError):
            tl.verify_checkpoint(bad0, vks_bad)

        # Leading zero / non-ASCII digits in size rejected
        text, sig = ok0.split("\n\n", 1)
        with self.assertRaises(ValueError):
            tl.verify_checkpoint(text.replace("\n0\n", "\n00\n", 1) + "\n\n" + sig, vks)

        # >63 proof hashes rejected in inclusion and consistency
        self.assertFalse(tl.verify_inclusion(b"x" * 32, 0, 2, [b"\x00" * 32] * 64, empty_root))
        self.assertFalse(tl.verify_consistency(1, 2, [b"\x00" * 32] * 64, empty_root, empty_root))
        self.assertTrue(tl.verify_consistency(0, 0, [], empty_root, empty_root))
        self.assertFalse(tl.verify_consistency(0, 0, [], b"\x00" * 32, b"\x00" * 32))

    def test_gauntlet_rollback_and_split_view_rejected(self):
        r1 = hashlib.sha256(b"a").digest()
        r2 = hashlib.sha256(b"b").digest()
        # Same size, different root
        self.assertFalse(tl.verify_consistency(5, 5, [], r1, r2))
        # Rollback (new_size < old_size)
        self.assertFalse(tl.verify_consistency(5, 4, [], r1, r2))
        n5, vks = self._signed_note("example.test/rb", 5, r1)
        # Mutate signed note to a different root at same size -> bad sig / mismatch
        n5_alt, _ = self._signed_note("example.test/rb", 5, r2)
        with self.assertRaises(ValueError):
            tl.verify_consistency_file(n5, n5_alt, {
                "type": tl.PROOF_TYPE, "origin": "example.test/rb",
                "old_size": 5, "old_root": r1.hex(),
                "new_size": 5, "new_root": r2.hex(), "proof": []
            }, vks)

    def test_all_23_leaves_and_manifest_verify(self):
        leaves = tl.TransparencyLog(os.path.join(LOG, "leaves.jsonl")).leaves
        self.assertEqual(len(leaves), 23)
        manifest = json.loads(_read("leaves_manifest.json"))
        self.assertEqual(len(manifest["entries"]), 23)
        root23 = bytes.fromhex(ROOT23)
        for i, (leaf, entry) in enumerate(zip(leaves, manifest["entries"])):
            self.assertEqual(entry["index"], i)
            self.assertEqual(entry["leaf"], leaf.hex())
            proof = tl.inclusion_proof(i, leaves)
            self.assertTrue(tl.verify_inclusion(leaf, i, 23, proof, root23), i)
        self.assertEqual(manifest["entries"][22]["id"], "goodfire-sdxl-7446")
        self.assertEqual(manifest["entries"][22]["manifest_sha256"],
                         "11042e952f05143b69f9da5db093c2f8867dc38d3b7da226a14110169b62d71a")

if __name__ == "__main__":
    unittest.main(verbosity=2)
