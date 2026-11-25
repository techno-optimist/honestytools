# SPDX-License-Identifier: Apache-2.0
"""verifier/ mirror: the bytes match SOURCE.sha256; the RFC 6962 log primitives work (stdlib only);
with `cryptography` installed, the two real fixtures verify offline, a one-byte edit fails, an
unpinned key fails, the shipped selftests pass, and the signed genesis checkpoint and its Rekor
anchor verify under the key published in keys/."""
import copy, hashlib, importlib, json, os, subprocess, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VDIR = os.path.join(ROOT, "verifier")
FIX = os.path.join(ROOT, "tests", "fixtures", "verifier")
try:
    import cryptography  # noqa: F401
    HAVE_CRYPTO = True
except Exception:
    HAVE_CRYPTO = False
try:
    import sympy  # noqa: F401
    HAVE_SYMPY = True
except Exception:
    HAVE_SYMPY = False

# leaf hashes recorded when the fixtures were fetched from www.honesty.tools on 2026-10-26
LEAVES = {"read_db8f162c66c3.json": "823b89a90ed6b734d7b7962019c063b9bd6942f7e54db69f92de8b81fadbce2f",
          "cert_ad55072b6a30.json": "27b52ff512f562a1580cd085e9bc890aa33a70a812f3fe47be9247b764d3e1fb"}


def _imp(name):
    if VDIR not in sys.path:
        sys.path.insert(0, VDIR)
    return importlib.import_module(name)


def _run(*args):
    return subprocess.run([sys.executable] + list(args), cwd=VDIR, capture_output=True, text=True, timeout=300)


class Manifest(unittest.TestCase):
    def test_bytes_match_source_manifest(self):
        with open(os.path.join(VDIR, "SOURCE.sha256")) as f:
            lines = [l.split() for l in f if l.strip()]
        self.assertGreaterEqual(len(lines), 3)
        for digest, name in lines:
            with open(os.path.join(VDIR, name), "rb") as g:
                self.assertEqual(hashlib.sha256(g.read()).hexdigest(), digest, name)


class MerkleLog(unittest.TestCase):
    def setUp(self):
        self.t = _imp("transparency_log")
        self.leaves = [hashlib.sha256(b"leaf-%d" % i).digest() for i in range(13)]

    def test_inclusion_all_and_forged(self):
        t, L = self.t, self.leaves
        root = t.merkle_root(L)
        for i in range(len(L)):
            self.assertTrue(t.verify_inclusion(L[i], i, len(L), t.inclusion_proof(i, L), root))
        self.assertFalse(t.verify_inclusion(b"x" * 32, 4, len(L), t.inclusion_proof(4, L), root))

    def test_consistency_and_rewrite(self):
        t, L = self.t, self.leaves
        root = t.merkle_root(L)
        for m in range(1, len(L)):
            self.assertTrue(t.verify_consistency(m, len(L), t.consistency_proof(m, L), t.merkle_root(L[:m]), root))
        rewritten = L[:3] + [b"y" * 32] + L[4:]
        self.assertFalse(t.verify_consistency(6, len(L), t.consistency_proof(6, L), t.merkle_root(L[:6]),
                                              t.merkle_root(rewritten)))

    def test_rfc6962_small_tree(self):
        # RFC 6962 2.1: MTH({}) = SHA-256(), MTH({d0}) = SHA-256(0x00 || d0), MTH(D[2]) = SHA-256(0x01 || l || r)
        t = self.t
        h = lambda b: hashlib.sha256(b).digest()
        self.assertEqual(t.merkle_root([]), h(b""))
        self.assertEqual(t.merkle_root([b"a"]), h(b"\x00a"))
        self.assertEqual(t.merkle_root([b"a", b"b"]), h(b"\x01" + h(b"\x00a") + h(b"\x00b")))


@unittest.skipUnless(HAVE_CRYPTO, "needs `cryptography` (pip install cryptography)")
class Receipts(unittest.TestCase):
    def setUp(self):
        self.v = _imp("aperture_verify")

    def _obj(self, name):
        return self.v._load(os.path.join(FIX, name))

    def test_fixtures_verify_offline(self):
        for name, leaf in LEAVES.items():
            rep = self.v.verify_receipt(self._obj(name))
            self.assertTrue(rep["ok"], name)
            self.assertEqual(rep["leaf"], leaf, name)
            self.assertIn(rep["key"], self.v.PINNED_KEYS)

    def test_one_byte_edit_fails(self):
        for name in LEAVES:
            o = copy.deepcopy(self._obj(name))
            k = next(k for k, val in o.items() if isinstance(val, str) and k not in ("id",))
            o[k] = o[k] + " "
            with self.assertRaises(self.v.VerifyError):
                self.v.verify_receipt(o)

    def test_unpinned_key_fails(self):
        o = self._obj("read_db8f162c66c3.json")
        with self.assertRaises(self.v.VerifyError):
            self.v.verify_receipt(o, pinned={"AAAA": "not-the-key"})

    def test_cli_verify_exit_codes(self):
        self.assertEqual(_run("aperture_verify.py", "verify", os.path.join(FIX, "read_db8f162c66c3.json")).returncode, 0)

    def test_selftests(self):
        for f in ("aperture_verify.py", "transparency_log.py"):
            r = _run(f, "selftest")
            self.assertEqual(r.returncode, 0, f + r.stdout[-400:] + r.stderr[-400:])

    @unittest.skipUnless(HAVE_SYMPY, "aperture_witness math cell needs sympy (56/58 without it)")
    def test_witness_selftest(self):
        r = _run("aperture_witness.py", "selftest")
        self.assertEqual(r.returncode, 0, r.stdout[-800:])


@unittest.skipUnless(HAVE_CRYPTO, "needs `cryptography` (pip install cryptography)")
class Anchor(unittest.TestCase):
    """The genesis checkpoint verifies under the pinned receipt-log key, its leaves rebuild the root,
    the Rekor v2 entry anchors it, and the key in keys/ is the one the verifier pins."""
    def setUp(self):
        self.t = _imp("transparency_log")
        self.note = open(os.path.join(VDIR, "log", "checkpoints", "22")).read()
        self.entry = json.load(open(os.path.join(VDIR, "log", "rekor-entry-0.json")))

    def test_keys_dir_matches_pin(self):
        vk = open(os.path.join(ROOT, "keys", "receipt-log.vkey")).read().strip()
        self.assertEqual(list(self.t.PINNED_LOG_VKEYS), [vk])
        pub = json.load(open(os.path.join(ROOT, "keys", "receipt-log.pub.json")))
        self.assertEqual(pub["vkey"], vk)
        _, _, raw = self.t.parse_vkey(vk)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), pub["fingerprint_sha256_raw"])

    def test_checkpoint_and_leaves(self):
        cp = self.t.verify_checkpoint(self.note)
        self.assertEqual((cp["tree_size"], cp["root"]),
                         (22, "890d25bd770f265415256a5fda48013a25ff78fc543711e2785376ad05c8ae71"))
        r = _run("transparency_log.py", "verify-checkpoint", "log/checkpoints/22", "log/leaves.jsonl")
        self.assertEqual(r.returncode, 0, r.stdout[-400:] + r.stderr[-400:])

    def test_rekor_anchor(self):
        rep = self.t.verify_rekor_anchor(self.note, self.entry)
        self.assertEqual(rep["rekor"]["log_index"], 171316570)

    def test_tampered_checkpoint_fails(self):
        bad = self.note.replace("\n22\n", "\n23\n", 1)
        self.assertNotEqual(bad, self.note)
        with self.assertRaises(Exception):
            self.t.verify_checkpoint(bad)
        e = copy.deepcopy(self.entry)
        e["logIndex"] = str(int(e["logIndex"]) + 1)
        with self.assertRaises(Exception):
            self.t.verify_rekor_anchor(self.note, e)


    def test_checkpoint_23_consistency_and_all_23_leaves(self):
        c23 = open(os.path.join(VDIR, "log", "checkpoints", "23")).read()
        cp = self.t.verify_checkpoint(c23)
        self.assertEqual((cp["tree_size"], cp["root"]),
                         (23, "97d2ecae83e6e9b2c06fb0f87110b277f5e8a216c07658d988222c57b8c5d72b"))
        self.assertEqual(c23, open(os.path.join(VDIR, "log", "checkpoint")).read())
        r = _run("transparency_log.py", "verify-checkpoint", "log/checkpoint", "log/leaves.jsonl")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        e1 = json.load(open(os.path.join(VDIR, "log", "rekor-entry-1.json")))
        self.assertEqual(self.t.verify_rekor_anchor(c23, e1)["rekor"]["log_index"], 171750012)
        proof = json.load(open(os.path.join(VDIR, "log", "proofs", "consistency-22-23.json")))
        self.assertTrue(self.t.verify_consistency_file(self.note, c23, proof)["ok"])
        manifest = json.load(open(os.path.join(VDIR, "log", "leaves_manifest.json")))
        leaves = self.t.TransparencyLog(os.path.join(VDIR, "log", "leaves.jsonl")).leaves
        self.assertEqual(len(leaves), 23)
        self.assertEqual(len(manifest["entries"]), 23)
        self.assertEqual(manifest["entries"][22]["id"], "goodfire-sdxl-7446")

    def test_cosignatures_22_and_23_and_strict_gauntlet(self):
        import base64, shutil, tempfile
        r22 = _run("verify_cosignatures.py", "log/cosigned/22")
        self.assertEqual(r22.returncode, 0, r22.stdout + r22.stderr)
        r23 = _run("verify_cosignatures.py", "log/cosigned/23")
        self.assertEqual(r23.returncode, 0, r23.stdout + r23.stderr)
        self.assertIn("rgdd.se/poc-witness co-signed", r23.stdout)
        self.assertIn("markovianprotocol.com/witness co-signed", r23.stdout)
        rc = _run("tests/test_consistency.py")
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)

        # Also verify verify_cosignatures.py rejects non-canonical b64 and stray chars
        orig = open(os.path.join(VDIR, "log", "cosigned", "23"), encoding="utf-8").read()
        body, sigs = orig.split("\n\n", 1)
        lines = sigs.splitlines()
        alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "log", "cosigned"))
            os.makedirs(os.path.join(d, "log", "checkpoints"))
            shutil.copy(os.path.join(VDIR, "log", "witnesses.txt"), os.path.join(d, "log", "witnesses.txt"))
            shutil.copy(os.path.join(VDIR, "log", "checkpoints", "23"), os.path.join(d, "log", "checkpoints", "23"))
            for idx in range(3):
                parts = lines[idx].split(" ")
                pad = len(parts[2]) - len(parts[2].rstrip("="))
                pos = len(parts[2]) - pad - 1
                nc = parts[2][:pos] + alpha[alpha.index(parts[2][pos]) ^ 1] + parts[2][pos + 1:]
                for tag, bad_sig in (("nc", nc), ("stray", "#!" + parts[2][:8] + "*" + parts[2][8:])):
                    mut = list(lines)
                    mut[idx] = " ".join(parts[:2] + [bad_sig])
                    fp = os.path.join(d, "log", "cosigned", f"23_{idx}_{tag}")
                    with open(fp, "w", encoding="utf-8") as fh:
                        fh.write(body + "\n\n" + "\n".join(mut) + "\n")
                    rr = _run("verify_cosignatures.py", fp)
                    self.assertEqual(rr.returncode, 1, (idx, tag, rr.stdout, rr.stderr))

if __name__ == "__main__":
    unittest.main()
