# SPDX-License-Identifier: Apache-2.0
"""Tests for witness/: the C2SP tlog-witness client and the append-only consistency tool.

Runs offline in CI against an in-process fake witness written from the spec (C2SP tlog-witness
add-checkpoint), with throwaway test logs. The interop check against a real independent witness
implementation (litewitness) is witness/e2e/run_litewitness_e2e.sh.
"""
import contextlib
import http.server
import io
import os
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "witness"))

import c2sp  # noqa: E402
import log_consistency  # noqa: E402
import testlog  # noqa: E402
import witness_client  # noqa: E402

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

GENESIS = os.path.join(ROOT, "witness", "archive", "size-22")


class FakeWitness:
    """Minimal spec witness: verifies the log signature and consistency proof, keeps per-origin state."""

    def __init__(self, logs):
        self.logs = dict(logs)  # origin -> vkey
        self.state = {}         # origin -> (size, root)
        self.sk = Ed25519PrivateKey.generate()
        pub = self.sk.public_key().public_bytes_raw()
        self.name = "fake.invalid/witness"
        self.kid = c2sp.key_id(self.name, 0x04, pub)
        self.vkey = "%s+%s+%s" % (self.name, self.kid.hex(), c2sp.b64(b"\x04" + pub))
        self.requests = []

    def handle(self, body):
        self.requests.append(body)
        text = body.decode()
        head, rest = text.split("\n", 1)
        old = int(head.split(" ")[1])
        proof = []
        while not rest.startswith("\n"):
            line, rest = rest.split("\n", 1)
            proof.append(c2sp.b64dec(line))
        note = rest[1:]
        origin = note.split("\n", 1)[0]
        if origin not in self.logs:
            return 404, "text/plain", b"unknown log\n"
        try:
            cp = c2sp.verify_log_checkpoint(note, self.logs[origin])
        except ValueError:
            return 403, "text/plain", b"bad signature\n"
        if old > cp["size"]:
            return 400, "text/plain", b"old > size\n"
        cur_size, cur_root = self.state.get(origin, (0, c2sp.merkle_root([])))
        if old != cur_size:
            return 409, "text/x.tlog.size", b"%d\n" % cur_size
        if old == 0 and proof:
            return 422, "text/plain", b"proof with old 0\n"
        if old == cp["size"] and cp["root"] != cur_root:
            return 422, "text/plain", b"same size different root\n"
        if 0 < old < cp["size"] and not c2sp.verify_consistency(old, cp["size"], proof, cur_root, cp["root"]):
            return 422, "text/plain", b"bad consistency proof\n"
        self.state[origin] = (cp["size"], cp["root"])
        ts = int(time.time())
        sig = self.sk.sign(c2sp.cosignature_message(origin, cp["size"], cp["root"], ts))
        line = "%s %s %s\n" % (c2sp.EM_DASH, self.name, c2sp.b64(self.kid + ts.to_bytes(8, "big") + sig))
        return 200, "text/plain", line.encode()


def serve(fw):
    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            code, ctype, out = fw.handle(body) if self.path == "/add-checkpoint" else (404, "text/plain", b"")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = witness_client.main(argv)
    return rc, out.getvalue()


class WitnessClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.origin = "test.invalid/unit-log"
        self.lv = testlog.make_log(os.path.join(self.d, "log"), self.origin, [5, 22, 37], fork_at=3, fork_size=40)
        self.fw = FakeWitness({self.origin: self.lv})
        self.srv, self.url = serve(self.fw)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def sub(self, size, state, extra=(), fork=False):
        L = os.path.join(self.d, "log", "fork" if fork else "")
        return run(["submit", "--log-vkey", self.lv, "--leaves-url", "file://" + os.path.join(L, "leaves.jsonl"),
                    "--checkpoint-url", "file://" + os.path.join(L, "checkpoint-%d" % size),
                    "--state-dir", os.path.join(self.d, state), "--witness-url", self.url,
                    "--witness-vkey", self.fw.vkey] + list(extra))

    def test_old_zero_then_409_then_consistency_proof(self):
        rc, out = self.sub(5, "a")
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.fw.requests[0].startswith(b"old 0\n\n"))
        rc, out = self.sub(37, "b")  # fresh state: sends old 0, gets 409 "5", retries with a proof
        self.assertEqual(rc, 0, out)
        self.assertIn("409", out)
        self.assertTrue(self.fw.requests[-1].startswith(b"old 5\n"))
        self.assertEqual(self.fw.state[self.origin][0], 37)
        saved = os.listdir(os.path.join(self.d, "b", "test.invalid_unit-log", "cosigned"))
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0].startswith("37-"))

    def test_state_file_supplies_old_size(self):
        self.assertEqual(self.sub(5, "a")[0], 0)
        n = len(self.fw.requests)
        rc, out = self.sub(22, "a")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.fw.requests), n + 1)  # no 409 round trip
        self.assertTrue(self.fw.requests[-1].startswith(b"old 5\n"))

    def test_fork_is_rejected_422(self):
        self.assertEqual(self.sub(37, "a")[0], 0)
        rc, out = self.sub(40, "f", ["--old-size", "37"], fork=True)
        self.assertEqual(rc, 6, out)

    def test_witness_ahead_of_us_is_an_alarm(self):
        self.assertEqual(self.sub(37, "a")[0], 0)
        rc, out = self.sub(22, "b")
        self.assertEqual(rc, 5, out)
        self.assertIn("ALARM", out)

    def test_dry_run_sends_nothing(self):
        rc, out = self.sub(5, "a", ["--dry-run"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.fw.requests, [])
        self.assertIn("old 0", out)

    def test_unknown_witness_key_cosignature_is_rejected(self):
        other = FakeWitness({}).vkey  # a different witness key: the 200 body has no line for it
        L = os.path.join(self.d, "log")
        rc, out = run(["submit", "--log-vkey", self.lv, "--leaves-url", "file://" + L + "/leaves.jsonl",
                       "--checkpoint-url", "file://" + L + "/checkpoint-5", "--state-dir", self.d + "/x",
                       "--witness-url", self.url, "--witness-vkey", other])
        self.assertNotEqual(rc, 0, out)

    def test_production_origin_only_from_published_urls(self):
        rc, out = run(["submit", "--checkpoint-url", "file://" + GENESIS + "/checkpoint", "--dry-run",
                       "--witness-url", self.url, "--witness-vkey", self.fw.vkey])
        self.assertEqual(rc, 2, out)
        self.assertIn("published", out)


class ConsistencyToolTest(unittest.TestCase):
    def test_genesis_archive_matches_repo_and_pinned_key(self):
        with open(os.path.join(GENESIS, "checkpoint")) as f:
            cp = c2sp.verify_log_checkpoint(f.read(), c2sp.PRODUCTION_VKEY)
        self.assertEqual((cp["origin"], cp["size"]), ("www.honesty.tools/receipts", 22))
        with open(os.path.join(GENESIS, "leaves.jsonl"), "rb") as f:
            self.assertEqual(c2sp.merkle_root(c2sp.parse_leaves_jsonl(f.read())), cp["root"])
        import hashlib
        with open(os.path.join(GENESIS, "SHA256SUMS")) as f:
            for line in f:
                h, name = line.split()
                with open(os.path.join(GENESIS, name), "rb") as g:
                    self.assertEqual(hashlib.sha256(g.read()).hexdigest(), h, name)

    def test_prove_and_verify(self):
        with tempfile.TemporaryDirectory() as d:
            lv = testlog.make_log(d, "test.invalid/c", [5, 37], fork_at=3, fork_size=40)
            q = lambda *a: run_lc(list(a))  # noqa: E731
            rc, out = q("prove", "--log-vkey", lv, "--old", d + "/checkpoint-5", "--old-leaves", "",
                        d + "/checkpoint-37", d + "/leaves.jsonl", "--proof-out", d + "/p.json")
            self.assertEqual(rc, 0, out)
            self.assertIn("APPEND-ONLY", out)
            rc, out = q("verify", "--log-vkey", lv, "--old", d + "/checkpoint-5", d + "/checkpoint-37", d + "/p.json")
            self.assertEqual(rc, 0, out)
            rc, out = q("prove", "--log-vkey", lv, "--old", d + "/checkpoint-5", "--old-leaves", "",
                        d + "/fork/checkpoint-40", d + "/fork/leaves.jsonl")
            self.assertEqual(rc, 1, out)
            self.assertIn("NOT APPEND-ONLY", out)

    def test_genesis_against_itself(self):
        rc, out = run_lc(["prove", GENESIS + "/checkpoint", GENESIS + "/leaves.jsonl"])
        self.assertEqual(rc, 0, out)


def run_lc(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = log_consistency.main(argv)
    return rc, out.getvalue()


if __name__ == "__main__":
    unittest.main()
