#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# End-to-end interop test: our Python client against litewitness (filippo.io/torchwood), an independent
# Go implementation of C2SP tlog-witness that several Witness Network testing-list witnesses run.
# Everything is local: the witness listens on 127.0.0.1 with an EPHEMERAL key. No real witness is contacted.
# Network use is read-only: the testing list (to configure our log exactly as real witnesses do) and our
# published checkpoint + leaves.
#
#   go install filippo.io/torchwood/cmd/litewitness@latest filippo.io/torchwood/cmd/witnessctl@latest
#   witness/e2e/run_litewitness_e2e.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
GOBIN="$(go env GOPATH)/bin"
LW="${LITEWITNESS:-$GOBIN/litewitness}"; WC="${WITNESSCTL:-$GOBIN/witnessctl}"
PORT="${PORT:-7381}"
T="$(mktemp -d)"; PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done; rm -rf "$T"; }
trap cleanup EXIT
pass=0; fail=0
check() { # name expected_rc actual_rc
  if [ "$2" = "$3" ]; then echo "PASS  $1 (exit $3)"; pass=$((pass+1)); else echo "FAIL  $1 (expected $2, got $3)"; fail=$((fail+1)); fi
}

(cd witness/e2e/testagent && go build -o "$T/testagent" .)
"$T/testagent" -sock "$T/agent.sock" -name e2e.invalid/witness > "$T/agent.out" & PIDS+=($!)
for _ in $(seq 50); do grep -q VKEY "$T/agent.out" 2>/dev/null && break; sleep 0.1; done
FP=$(sed -n 's/^FINGERPRINT=//p' "$T/agent.out"); WVKEY=$(sed -n 's/^VKEY=//p' "$T/agent.out")

# configure our production log the way testing-list witnesses do: pull the public list
if ! "$WC" pull-logs -db "$T/w.db" -source https://testing.witness-network.org/log-list.1 >/dev/null 2>&1; then
  echo "(could not pull the testing list; configuring the pinned vkey by hand)"
  "$WC" add-log -db "$T/w.db" -origin www.honesty.tools/receipts
  "$WC" add-key -db "$T/w.db" -origin www.honesty.tools/receipts -key "www.honesty.tools/receipts+fcd79bdd+AUF/J2nQKn90Ba52NjK0jqgIYayocRQGAYsK7PUdD7+C"
fi
"$WC" list-logs -db "$T/w.db" | grep -q '"www.honesty.tools/receipts"' || { echo "our log is not on the list?"; exit 1; }

TL="$T/tl"; TLV=$(python3 witness/testlog.py "$TL" --origin test.invalid/e2e-log --sizes 5,22,37 --fork-at 3 --fork-size 40)
"$WC" add-log -db "$T/w.db" -origin test.invalid/e2e-log >/dev/null 2>&1
"$WC" add-key -db "$T/w.db" -origin test.invalid/e2e-log -key "$TLV" >/dev/null 2>&1

"$LW" -name e2e.invalid/witness -key "$FP" -ssh-agent "$T/agent.sock" -db "$T/w.db" -listen "127.0.0.1:$PORT" > "$T/lw.log" 2>&1 & PIDS+=($!)
for _ in $(seq 50); do grep -q listening "$T/lw.log" && break; sleep 0.1; done
grep -q "vkey=$WVKEY" "$T/lw.log" && { echo "PASS  litewitness reports the same witness vkey we derived"; pass=$((pass+1)); } || { echo "FAIL vkey mismatch"; fail=$((fail+1)); }

W=(--witness-url "http://127.0.0.1:$PORT" --witness-vkey "$WVKEY")
C() { python3 witness/witness_client.py "$@"; }
TLC() { local s=$1 st=$2; shift 2; C submit --log-vkey "$TLV" --leaves-url "file://$TL/leaves.jsonl" --checkpoint-url "file://$TL/checkpoint-$s" --state-dir "$T/$st" "${W[@]}" "$@"; }

set +e
C submit --state-dir "$T/p0" --expect-size 22 "${W[@]}" --dry-run >/dev/null;             check "production: dry run" 0 $?
C submit --state-dir "$T/p1" --expect-size 23 "${W[@]}" >/dev/null 2>&1;                    check "production: --expect-size mismatch refuses" 2 $?
C submit --state-dir "$T/p1" --checkpoint-url "file://$PWD/witness/archive/size-22/checkpoint" "${W[@]}" >/dev/null 2>&1
                                                                                            check "production: refuses non-published checkpoint source" 2 $?
C submit --state-dir "$T/p1" --expect-size 22 "${W[@]}";                                    check "production: real size-22 checkpoint, old 0 -> 200 + verified cosignature" 0 $?
C submit --state-dir "$T/p2" --expect-size 22 "${W[@]}" | tee "$T/o"; r=${PIPESTATUS[0]}
grep -q "409 Conflict" "$T/o";                                                              check "production: fresh state gets 409 (witness at 22)" 0 $?
                                                                                            check "production: 409 -> retry old 22 -> 200" 0 $r
TLC 5 t1;                                                                                   check "test log: size 5, old 0 -> 200" 0 $?
TLC 37 t2 | tee "$T/o"; r=${PIPESTATUS[0]}
grep -q "retrying with old 5 and a 7-line" "$T/o";                                          check "test log: 409 -> 7-hash consistency proof 5->37" 0 $?
                                                                                            check "test log: proof accepted by litewitness -> 200" 0 $r
TLC 22 t3 >/dev/null 2>&1;                                                                  check "test log: witness ahead of us (37 > 22) -> ALARM" 5 $?
C submit --log-vkey "$TLV" --leaves-url "file://$TL/fork/leaves.jsonl" --checkpoint-url "file://$TL/fork/checkpoint-40" \
  --state-dir "$T/t4" --old-size 37 "${W[@]}" >/dev/null 2>&1;                              check "test log: forked history -> litewitness 422" 6 $?
python3 witness/log_consistency.py prove --log-vkey "$TLV" --old "$TL/checkpoint-5" --old-leaves "" "$TL/checkpoint-37" "$TL/leaves.jsonl" --proof-out "$T/p.json" >/dev/null
                                                                                            check "consistency tool: prove 5->37" 0 $?
python3 witness/log_consistency.py verify --log-vkey "$TLV" --old "$TL/checkpoint-5" "$TL/checkpoint-37" "$T/p.json" >/dev/null
                                                                                            check "consistency tool: verify proof file" 0 $?
python3 witness/log_consistency.py prove --log-vkey "$TLV" --old "$TL/checkpoint-5" --old-leaves "" "$TL/fork/checkpoint-40" "$TL/fork/leaves.jsonl" >/dev/null
                                                                                            check "consistency tool: fork is NOT append-only" 1 $?
python3 witness/log_consistency.py prove witness/archive/size-22/checkpoint witness/archive/size-22/leaves.jsonl >/dev/null
                                                                                            check "consistency tool: genesis archive vs itself" 0 $?
set -e
echo "---- $pass passed, $fail failed"
[ "$fail" = 0 ]
