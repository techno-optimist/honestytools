# Aperture deterministic witnesses — open spec (BUILD-READY, post-adversarial-review)

`aperture.witness/v1` · extends `aperture.receipt.verify/v1` · draft 2026-07-18 rev B
(rev B folds four adversarial critiques: envelope binding, sandbox MUST, registry
equivocation, lattice repair, per-cell verdicts, registry-substrate honesty)

## 0. Purpose and lineage

Today the cert layer issues **statistical** certificates: `lucidia/cert/conformal.py` (Clopper-Pearson
UCB + RCPS/LTT) and `lucidia/cert/multicell.py` (per-cell CP bounds) — whose own `bound_semantics`
says the guarantee is "a population-level marginal bound … NOT a per-answer probability of
correctness; not shift-robust." The arena machinery (PNT LP-SDP dual certificates, the Erdős
min-overlap any-n verifier, Ramsey/Hadamard SAT witnesses) issues **deterministic** certificates: an
artifact a stranger re-checks exactly, with no distribution and no trust in the issuer.

This spec transfers the second discipline onto the first: every answer served by a **fixed-rule
cell** (`multicell.cell_of`: `grounded_registry`, `code_verified`, `math_verified`) carries an exact
**per-answer witness** that is (a) deterministic, (b) offline-checkable with a dependency-light
verifier, and (c) cryptographically bound into the ed25519-signed receipt, so the witness and the
receipt cannot be mixed-and-matched.

**Out of scope, with the correct reasons for each:**

- `cheap_failscore` is excluded because it is a **tunable** cell (conformal path in
  `multicell.py`) — no fixed re-derivable rule exists.
- `escalated_agreement` is excluded for a **different** reason: `multicell.cell_of` classifies it
  as a FIXED-rule cell (">=2 cross-family frontiers agree"), but that rule is **not offline
  re-derivable by a stranger** — re-checking it means re-invoking non-deterministic, networked,
  costly frontier models. Fixed but not reproducible ⇒ no deterministic witness. (Do not read this
  spec as calling escalated_agreement "tunable"; it is not, per `multicell.py`.)

**A note on the word "witness."** In the arena lineage a witness certifies a mathematical TRUTH
(satisfiability, dual feasibility). In this spec "witness" means **evidence that the cell's fixed
rule fired and can be re-fired offline**. For `math_verified` Tier D the two senses coincide. For
`grounded_registry` they do NOT: that cell's witness certifies membership in a curated set and
nothing about the world. The per-cell verdict tokens (§D) exist so no consumer can confuse the two.

### Deliverables (revised: three, plus one prerequisite)

- **`Aperture/verifier/aperture_witness.py`** — the OFFLINE VERIFIER. Sits beside
  `aperture_verify.py`, reuses `transparency_log.py`, Python ≥ 3.9. Hard deps: `cryptography`.
  Optional dep: `sympy` (only for `math_verified`; absent ⇒ `UNCHECKABLE_HERE`, never a false
  PASS). Ships to honesty.tools/verifier like the flight recorder.
- **`Aperture/verifier/transparency_log.py` extension** — RFC 6962 **consistency proofs**
  (`consistency_proof` / `verify_consistency`, §A.1.3). Required by the registry-equivocation
  defense; currently the module has inclusion proofs only.
- **The reference EMITTER** — `witness_emitter.py`, testable end-to-end against a **mock**
  registry and mock answers. Production wiring is fork-gated.
- **PREREQUISITE (blocking `grounded_registry` in production): `REGISTRY_BUILD_SPEC.md`.**
  Honest status: **the registry substrate this cell presumes does not exist on this branch.**
  Today's "grounding" in `Aperture/web/engine.py` is a pack name-existence hit (HGNC/OEIS/CVE
  `registry_url` labels) fed to the LLM as a prompt hint — a name-hit, not a span-aligned
  fact-triple store. There is no fact schema, no epoch process, no registry signing key, no SRR
  publication. Consequence, stated plainly: **answers grounded only by name-hit + prompt
  discipline are NOT witnessable and MUST demote out of the `grounded_registry` cell.** The mock
  registry makes the verifier and conformance suite buildable today; production
  `grounded_registry` witnesses cannot exist until the substrate in `REGISTRY_BUILD_SPEC.md`
  (fact schema, epoch ceremony, distinct-key ceremony, SRR publication, external co-signature)
  is built.

Terminology: MUST / SHOULD / MAY as in RFC 2119. All canonical JSON in this spec means
`json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode("utf-8")` —
identical to `aperture_verify.canonical_body`.

---

## A. Witness schema — `aperture.witness/v1`

### A.0 Common envelope (all cells) — the WHOLE envelope is bound

```json
{
  "schema": "aperture.witness/v1",
  "cell": "grounded_registry | code_verified | math_verified",
  "answer_sha256": "<hex sha256 of the exact served answer bytes (utf-8)>",
  "emitted_at": "<ISO-8601 UTC>",
  "emitted_with": {"interpreter": "cpython-3.11.9"},
  "claim": { ... cell-specific: WHAT is being witnessed ... },
  "witness": { ... cell-specific: the re-checkable evidence ... }
}
```

The **witness hash** (the value bound into the receipt, §C) is computed over the **entire
envelope** — schema, cell, answer_sha256, emitted_at, emitted_with, claim, AND witness:

```
witness_sha256 = sha256( b"aperture.witness/v1\x00" + canonical_json(FULL_ENVELOPE) ).hexdigest()
```

> **Why the full envelope (rev B fix, was a soundness hole):** the previous draft hashed only the
> `witness` object. But `claim.asserted_values` is what the VERIFY driver iterates to decide which
> answer spans get checked — leaving it unsigned made the semantic assertion set malleable
> post-issuance (a holder could rewrite or empty `asserted_values` and still ed25519-verify).
> Binding the full envelope also signs `answer_sha256` and `emitted_at`, closing provenance-time
> spoofing. Conformance vector (§F): a one-byte tamper anywhere in the envelope, including inside
> `claim`, MUST be rejected.

Domain-separated (same discipline as `aperture_verify.leaf_hash`) so a witness hash can never
collide with a receipt leaf or a raw file hash.

**Span semantics (normative, all cells).** All spans (`answer_span`, `fact_span`,
`stdout_span`) are **utf-8 byte offsets into the raw, pre-normalization bytes** of the named
source. Implementations MUST slice bytes, not `str` code points. Normalization (`casefold` =
NFC + casefold; `numeric` = `decimal.Decimal` parse) is applied to the **extracted slice** on both
sides, after slicing. Out-of-range, overlapping, or non-byte-boundary spans ⇒ FAIL.

**Traceable fields are strings.** Every registry fact field that a trace may target MUST be a JSON
string (numeric constants stored as canonical decimal strings). Slicing a JSON number is
undefined and MUST be rejected.

### A.1 `grounded_registry` — Merkle inclusion proof of the registry fact

**Establishes (exactly): the cited fact is a member of the operator's published registry at the
signed epoch, and the asserted answer bytes equal that fact's field bytes under a declared
normalization.** Nothing more. Verdict token: `REGISTRY_MEMBERSHIP_OK` (§D) — deliberately NOT a
generic "witness ok."

#### A.1.1 The registry as a transparency-logged structure

- **Fact leaf.** Every registry fact is a canonical JSON object (stable field set:
  `{"fact_id", "subject", "predicate", "object", "unit", "source", "as_of"}`; `object` is a
  canonical string; `unit` is a string or `null`; the emitter pins the exact field list per
  registry snapshot). Its leaf-data is
  `sha256( b"aperture.registry.leaf/v1\x00" + canonical_json(fact) )` (32 bytes), fed to the
  RFC 6962 tree via `transparency_log._leaf_hash`, in ascending `fact_id` order so the root is a
  pure function of the fact set.
- **Signed registry root (SRR).**

```json
{
  "schema": "aperture.registry.root/v1",
  "epoch": "2026-07-18T00:00:00Z",
  "tree_size": 184233,
  "root_sha256": "<hex>",
  "leaf_order": "fact_id_ascending",
  "prev_epoch": "2026-07-11T00:00:00Z",
  "prev_tree_size": 183890,
  "trust": "operator_self_signed",
  "signature": {"alg": "ed25519", "pub": "<base64>", "sig": "<base64>"}
}
```

  Signed with the receipt recipe (canonical body = object minus `signature`) under a pinned
  **registry-root key that MUST be disjoint from every receipt-signing key** (§C key-role
  separation). The mandatory `trust: "operator_self_signed"` marker makes the self-attestation
  visible **in the artifact**, not only in prose: this signature is the operator vouching for its
  own fact set. The word "attested" or "independent" is reserved for the external-witness
  co-signature milestone (base SPEC §3), which is the only rung that moves trust outside the
  operator boundary — and which is a **release gate** for this cell (§D.3).

#### A.1.2 Witness body

```json
"claim": {
  "kind": "fact_grounding",
  "fact_id": "reg:phys:planck_constant",
  "asserted_values": [{"answer_span": [104, 130], "fact_field": "object", "norm": "exact"}]
},
"witness": {
  "fact": { ...full canonical fact JSON... },
  "leaf_index": 90211,
  "tree_size": 184233,
  "audit_path": ["<hex>", "..."],
  "registry_root": { ...the full SRR object incl. signature... },
  "consistency_path": ["<hex>", "..."],
  "token_trace": [
    {"answer_span": [104, 130], "fact_field": "object", "fact_span": [0, 26], "norm": "exact"}
  ],
  "unwitnessed_byte_ranges": [[0, 104], [130, 512]]
}
```

- `token_trace` links **answer → fact**: each entry says bytes `answer[a:b]` equal (under the
  declared normalization) bytes `fact[field][c:d]`. Allowed `norm` values, all deterministic:
  `"exact"`, `"casefold"`, `"numeric"` (`decimal.Decimal` equality — no float round-trip). Every
  entry in `claim.asserted_values` MUST have a matching trace entry.
- **What the trace does NOT establish (rev B, replacing a deleted overclaim).** The previous
  draft said "the load-bearing content of the answer is exactly the set of asserted spans." That
  claim is unverifiable and is **deleted**. Normative replacement: *the witness certifies ONLY
  the asserted spans. All unasserted bytes of the answer are UNVERIFIED. Byte-substring equality
  is NOT semantic entailment: negation, modality, or surrounding context ("It is a myth that
  [correctly-cited constant]…") can invert the answer's meaning while every trace entry passes.*
  The emitter MUST populate `unwitnessed_byte_ranges` (the complement of the asserted spans) and
  the verifier MUST surface "N unwitnessed bytes" in every report. No report string may imply the
  answer is "grounded" as a whole; the honest sentence is "the cited substring equals a registry
  fact value."
- **Units.** `norm: "numeric"` is admissible ONLY against fact fields declared unit-free
  (`unit: null`). For unit-bearing facts, the trace MUST cover the value **and** the unit: either
  one `exact`/`casefold` trace over the full value+unit string, or a numeric trace on the value
  plus a mandatory adjacent `casefold` trace binding the answer's unit token to `fact["unit"]`.
  A numeric-only trace against a unit-bearing fact ⇒ FAIL. Selftest carries a unit-mismatch
  vector (`6.626e-34 kg` vs `J·s` MUST fail).
- The customer verifies **with no registry copy**: the fact travels inside the witness; the audit
  path + signed root prove membership; ~log₂(N) hashes, offline.

#### A.1.3 Anti-equivocation: pinned checkpoint + consistency proof (MUST)

The previous draft let VERIFY accept any validly-signed SRR — so the operator could sign root A
over fact-set-1 for auditor X and root B over fact-set-2 for customer Y, both green. Rev B closes
the *silent-revision* half of that hole and states the residue honestly:

- The verifier ships with a **pinned SRR checkpoint** (epoch, tree_size, root_sha256) alongside
  `PINNED_KEYS` — same pinning discipline, updated on verifier release.
- VERIFY MUST check: (a) SRR signature under the pinned **registry-root** key (a receipt key here
  is a forgery, and vice versa); (b) `SRR.tree_size >= checkpoint.tree_size`; (c) the witness's
  `consistency_path` proves RFC 6962 §2.1.2 consistency from the checkpoint root/size to the
  presented SRR root/size (`verify_consistency`, new in `transparency_log.py`). Equal sizes ⇒
  roots MUST be byte-equal. Any failure ⇒ FAIL, named reason `registry_equivocation_or_rollback`.
- **Honest limit (ships in §E):** consistency-from-checkpoint proves the presented registry
  extends the pinned history. It does NOT detect equivocation between two verifiers pinned to
  *different* checkpoints, and cannot detect a wrong fact honestly appended. Cross-verifier
  equivocation detection requires gossip or the external co-signature — not yet implemented.
  Until the distinct registry key + external co-signature exist, `grounded_registry` MUST NOT be
  pinned into a released verifier (release gate, §D.3); it lives in selftest/mock only.

### A.2 `code_verified` — code + expected output, re-executed under ENFORCED isolation

**Establishes (exactly): the shipped program, run isolated on the verifier's machine, reproduces
the expected output byte-exactly, and the answer's asserted spans equal spans of that output.**
Verdict token: `CODE_EXEC_OK`.

```json
"claim": {
  "kind": "code_execution",
  "asserted_values": [{"answer_span": [12, 19], "binds_to": "stdout_span", "span": [0, 7]}]
},
"witness": {
  "language": "python",
  "interpreter": "cpython>=3.9,<4",
  "code": "<full source, utf-8>",
  "stdin": "",
  "expected_stdout": "<exact bytes the code must print>",
  "expected_exit": 0,
  "timeout_s": 30
}
```

- **`timeout_s` cap:** the verifier enforces a ceiling (`TIMEOUT_CEILING_S = 60`); a witness
  declaring more ⇒ FAIL (`timeout_exceeds_ceiling`). No witness-controlled DoS.
- **Isolation is a MUST, not a SHOULD (rev B fix, was a soundness hole).** The previous draft made
  the no-network container a SHOULD. That is fatal: witness code that FETCHES a constant equal to
  `expected_stdout` passes the emitter's double-run (stable server) and the un-isolated verifier —
  the code never *derived* anything, and "re-derived on the verifier's machine" becomes false.
  `python -I -E` blocks none of sockets, filesystem reads, subprocess, or clock. Normative rule:

  **The verifier MUST run witness code with OS-enforced no-network-egress, no-filesystem-write
  (outside a fresh tempdir), and resource limits — or it MUST return `UNCHECKABLE_HERE` for the
  cell. It MUST NOT run witness code without attested isolation and MUST NEVER emit a PASS from
  an unattested run.** Fail safe, not fail open.

  Implementation ladder (all attested in the report's `sandbox` field):
  1. Linux: `unshare -n` (or equivalent netns) + `resource` rlimits + tempdir cwd. Cheap, stdlib+OS.
  2. Any platform: the shipped container profile (`witness-sandbox.Dockerfile`, `--network=none`,
     read-only root, tmpfs workdir).
  3. Platform provides neither ⇒ `UNCHECKABLE_HERE`, reason `no_attestable_sandbox`.
- **Static gate (defense-in-depth, honestly labeled).** Before execution the verifier runs an AST
  pass rejecting imports outside an allowlist (`math`, `fractions`, `decimal`, `itertools`,
  `functools`, `collections`, `heapq`, `bisect`, `sys` (argv/stdout only), `re`, `json`) and
  rejecting `open`/`__import__`/`eval`/`exec`/`compile`/`socket`/`os`/`subprocess`/`time`/
  `random` (unseeded)/`ctypes`. **Stated limit: a Python AST gate is a conformance filter on
  witness shape, NOT a security boundary** — the OS-level isolation above is the security
  boundary; the gate exists so violations fail fast with a named reason and are selftestable.
- **Emitter determinism** remains an emitter conformance requirement (double-run tripwire, §B.1),
  now backed by the verifier-side isolation rather than trusted.
- **Output canon (rev B tightening).** Byte equality after normalizing line endings to `\n` and
  stripping one trailing newline, no float tolerance. The emitter MUST restrict printed values to
  a concrete allowlist: `print(int)`, `Fraction` as `p/q`, `Decimal` via explicit `quantize`,
  ASCII strings. The envelope records `emitted_with.interpreter`; on stdout mismatch the verifier
  names both versions in the reason (cross-version divergence is an availability risk — it fails
  closed — but the report must distinguish it from tampering).
- **Load-bearing ordering:** receipt signature and binding (§B.2 steps 1–2) run BEFORE any code
  executes. This ordering is a security property: the verifier only ever executes code that the
  pinned operator key signed. It bounds the attacker to the operator or a key compromise; it does
  not replace the sandbox.
- Residual (§E): a correct execution of the **wrong program** is a valid witness. Nothing here
  proves the code formalizes the user's question.

### A.3 `math_verified` — claim + certificate-shaped re-check (never search)

**Establishes (Tier D, exactly): the stated identity holds, verified by decision procedures on
the verifier's machine.** Verdict tokens: `MATH_TIER_D_OK` / `MATH_TIER_S_OK`.

The witness is a **checking script in a restricted step vocabulary** — the arena lesson: ship a
certificate cheap to *check*, never one that asks the verifier to *search*.

```json
"claim": {
  "kind": "math_identity",
  "statement": "Integral(x**2, (x, 0, 1)) == Rational(1, 3)",
  "asserted_values": [{"answer_span": [40, 43], "expr": "Rational(1,3)", "norm": "sympy_equal"}]
},
"witness": {
  "sympy": ">=1.12",
  "parser": "restricted",
  "steps": [
    {"op": "verify_antiderivative", "F": "x**3/3", "integrand": "x**2", "var": "x",
     "bounds": ["0", "1"], "expect": "Rational(1, 3)"}
  ]
}
```

- **Restricted parser — token gate BEFORE parse_expr (rev B fix).** The previous draft claimed
  "never eval"; but `sympy.parsing.sympy_parser.parse_expr` is eval-based **internally** (it
  compiles the transformed string). The whitelist therefore MUST be enforced by a tokenizer/AST
  pre-pass on the **raw expression string** before anything reaches `parse_expr`: only
  `[0-9a-zA-Z_+\-*/^(), .]` characters; every NAME token in the head whitelist (`Integer`,
  `Rational`, `Symbol`, single-letter symbols, `Add`, `Mul`, `Pow`, `binomial`, `factorial`,
  `gcd`, `isprime`, `expand`, `together`, `diff` — the enumerated set ships in code); length cap;
  reject attribute access and double-underscores. Signature-before-parse ordering (§A.2) applies
  here too and is load-bearing. `parse_expr` then runs with `eval=False`-style transformations
  on the pre-cleared string. The pre-pass, not `parse_expr`, is the boundary.
- **Tier D (decidable — restricted to genuine decision procedures, rev B fix).** The previous
  draft put `eval_integral` / `Integral.doit()` in Tier D. That was a mislabel: symbolic
  integration is a heuristic **search** (Risch/Meijer), version-fragile, with a documented
  history of sign/branch errors — a wrong SymPy closed form would have produced a false PASS
  wearing the strong tier. Tier D is now exactly:
  - exact integer/rational arithmetic;
  - polynomial/rational-function identity via `expand(lhs - rhs) == 0` (+ `together` for
    rational functions);
  - substitution checks (root check: substitute, get literal 0);
  - factor check (multiply provided factors, compare — verification, not factoring);
  - `verify_antiderivative` (the certificate-shaped replacement for `eval_integral`): witness
    ships the antiderivative `F`; verifier checks `expand(diff(F, var) - integrand) == 0`
    (mechanical) then `F(b) - F(a) == expect` in exact arithmetic. The emitter did the search;
    the verifier only differentiates — differentiation is algorithmic;
  - `verify_sum_closed_form`: witness ships the closed form `S(n)`; verifier checks the
    telescoping/induction identity `S(n) - S(n-1) == term(n)` via `expand == 0` plus the base
    case in exact arithmetic.
- **Tier S (semi-decision, opt-in, honestly weaker):** `simplify(lhs - rhs) == 0`, and bare
  `.doit()` on Integrals/Sums (i.e. anything that re-runs SymPy's heuristics). Reported as
  `MATH_TIER_S_OK`, never `MATH_TIER_D_OK`. **Stated in both directions (rev B):** a Tier-S
  FAIL is not a disproof, and a Tier-S PASS **can be a false PASS** on branch-cut /
  assumption-dependent expressions (`sqrt(x**2)`, `log(a*b)` vs `log a + log b`) or a SymPy
  bug — the guarantee rests on SymPy's simplifier, full stop. Tier S caps any composite it
  enters at the Tier-S level (§D) and MUST NOT appear in a composite a consumer treats as sound.
- **Reference Tier-D compiler scope (stated so nobody over-expects):** the reference emitter
  compiles only the enumerated taxonomy above (rational arithmetic, polynomial identity,
  root/factor checks, antiderivative/sum certificates). Claims outside it are demoted to Tier S
  or refused. A general claim→Tier-D compiler is future work, not this deliverable.
- Version pin: the witness records the SymPy constraint; the verifier reports the version it ran.

---

## B. EMIT + VERIFY algorithms

### B.1 EMIT (serving side — spec + reference impl, prod fork-gated)

Common: serve answer `A`; compute `answer_sha256 = sha256(A.encode()).hexdigest()`; build the
full envelope; compute `witness_sha256` over the FULL envelope (§A.0); put the binding block (§C)
into the receipt **before** signing; the receipt's signed body carries the full verbatim answer
(§C, mandatory); store/serve the witness alongside (inline if < 64 KiB, else by hash + URL).

- **grounded_registry** *(mock registry until REGISTRY_BUILD_SPEC substrate exists — §0)*.
  **Alignment algorithm (normative, was undefined):** for each candidate fact, for each traceable
  field, deterministically search the answer bytes: (1) exact byte match of the field value;
  (2) else NFC+casefold match; (3) else, for unit-free numeric fields, scan the answer's maximal
  decimal-number tokens and compare `Decimal` equality; unit-bearing fields additionally require
  the adjacent unit token per §A.1.2. Record spans; compute `unwitnessed_byte_ranges` as the
  complement. **If no field aligns, the answer is demoted out of the cell — emit-or-demote,
  never emit-approximately.** Then: `leaf_index` → `audit_path = inclusion_proof(...)` → attach
  current SRR + `consistency_path` from the published checkpoint. Audit paths are pure functions
  of the epoch and MAY be precomputed per-epoch and cached. The emitter MUST run
  `verify_inclusion`, `verify_consistency`, and the token-trace check on its own output before
  signing — a witness that does not self-verify is never emitted.
- **code_verified.** Emit the exact code + stdin + captured stdout as `expected_stdout`, printed
  values restricted to the §A.2 canon allowlist. The emitter MUST re-run the code **twice in the
  same isolation the verifier uses** and refuse to emit on any mismatch (nondeterminism
  tripwire — necessary, not sufficient: the verifier-side sandbox, not this tripwire, is the
  soundness mechanism). Record `emitted_with.interpreter`.
- **math_verified.** Compile the claim to Tier-D steps within the reference taxonomy (for an
  integral: run the search ONCE at emit time, ship the antiderivative); run the verifier's own
  `check_math` on the result before signing; refuse or downgrade to flagged Tier S if Tier-D
  compilation fails.

### B.2 VERIFY (customer side — `aperture_witness.py`, offline, deterministic)

`verify_witness(receipt, witness, *, pinned) -> report`. Steps 1–2 are **boolean gates** (pass ⇒
proceed; fail ⇒ hard FAIL with a named reason, mirroring `VerifyError`). Step 3 produces the
cell verdict. (This replaces the broken draft lattice — §D.)

1. **Receipt provenance (gate).** `aperture_verify.verify_receipt(receipt, pinned=pinned)` —
   ed25519 under a pinned key, byte-exact.
2. **Binding (gate).** Recompute `witness_sha256` over the presented FULL envelope; MUST equal
   `receipt["witness"]["witness_sha256"]` (inside the signed body). The receipt MUST carry the
   mandatory `answer` field (§C); recompute `sha256(answer bytes)`; MUST equal
   `witness["answer_sha256"]`. A witnessed receipt without the `answer` field ⇒ FAIL
   (`missing_answer_field`). This kills mix-and-match in both directions, including tampering
   with `claim.asserted_values` (bound since rev B).
3. **Cell check** — dispatch on `witness["cell"]`, output one of {FAIL, UNCHECKABLE_HERE, PASS}:
   - **grounded_registry:** (a) SRR signature under the pinned **registry-root** key (disjoint
     from receipt keys; either-key-crossover ⇒ forgery); (b) checkpoint + consistency proof per
     §A.1.3; (c) leaf hash + `verify_inclusion` with `tree_size`/`root` taken from the SRR;
     (d) token trace per §A.1.2 (byte-span slicing, norms, unit rules; reject unknown norms,
     overlapping/out-of-range spans, asserted values with no trace); (e) recompute and report
     `unwitnessed_byte_ranges`.
   - **code_verified:** enforce `timeout_s <= 60`; static AST gate; establish OS-level isolation
     per §A.2 or return `UNCHECKABLE_HERE` (`no_attestable_sandbox`); execute; compare canon
     stdout + exit; check answer-span bindings against actual stdout; report the attested
     `sandbox` mode.
   - **math_verified:** token-gate then parse every expression (unknown token ⇒ FAIL); execute
     steps in order; every check MUST come out true; record achieved tier. SymPy absent ⇒
     `UNCHECKABLE_HERE`.
4. **Report** (schema in §D.2) — including verdict token, `establishes`, per-cell
   `residual_trust`, checker attestation, and the mandatory footer (§E).

Everything in step 3 is a pure function of `(receipt, witness, pinned keys, pinned checkpoint)` —
no network. The only outbound bytes a conformant verifier ever produces are none.

---

## C. How `aperture_witness.py` extends `aperture_verify.py`

**Receipt extension (additive, backwards-compatible).** A fixed-rule-cell receipt gains, inside
the **signed body**:

```json
"answer": "<the FULL verbatim served answer, utf-8, untruncated — MANDATORY for witnessed receipts>",
"witness": {
  "schema": "aperture.witness/v1",
  "cell": "grounded_registry",
  "witness_sha256": "<hex>",
  "inline": { ...full witness envelope, OPTIONAL if small... },
  "fetch": "https://www.honesty.tools/api/witness/<witness_sha256>"
}
```

- **`answer` is mandatory (rev B fix, was unbuildable without it):** every span check and the
  answer-hash binding require the exact full served bytes. A witnessed receipt whose signed body
  lacks the verbatim `answer` field is FAIL (`missing_answer_field`). This is a hard requirement
  on the serving fork; the base `aperture.receipt.verify/v1` (unwitnessed) is unchanged.
- **Key-role separation (MUST):** the pin set for receipt-signing keys and the pin set for the
  registry-root key MUST be disjoint. An SRR signed by a receipt key is a forgery; a receipt
  signed by the registry key is a forgery. One compromised path never mints the other's artifacts.

Because the `witness` block is inside the signed body, the existing recipe signs the binding for
free — no new signature scheme. Receipts without a `witness` field verify exactly as today
(`aperture_verify.py` untouched). `aperture_witness.py` imports `verify_receipt`,
`canonical_body`, `leaf_hash`, `PINNED_KEYS`, plus `merkle_root` / `inclusion_proof` /
`verify_inclusion` / `consistency_proof` / `verify_consistency` from `transparency_log.py`.

Module surface:

```
aperture_witness.py
  verify_witness(receipt, witness=None, *, pinned=None, checkpoint=None) -> report
  witness_hash(envelope_obj) -> hex        # domain-separated, FULL envelope
  check_registry(witness, answer_bytes, pinned, checkpoint) -> cellresult
  check_code(witness, answer_bytes) -> cellresult      # sandboxed or UNCHECKABLE_HERE
  check_math(witness, answer_bytes) -> cellresult      # optional-sympy
  CLI: verify <receipt.json> [witness.json] | hash <witness.json> | selftest
```

The `fetch` URL is a convenience mirror only: a fetched witness is checked against the signed
`witness_sha256` before use — trust never moves to the network.

---

## D. Verdicts, composition, and the soundness contract

### D.1 Structure (rev B — replaces the broken draft lattice)

> The previous draft ordered `PROVENANCE_OK < WITNESS_OK` in one chain and reported the meet of
> {signature, binding, cell} — under which a fully green verification could only ever reach
> `PROVENANCE_OK` and the top verdict was unreachable, contradicting its own conformance vector.
> Fixed as follows.

**Per-receipt verdict = gates + cell label.** Receipt signature and binding are boolean **gates**
(fail ⇒ `FAIL`, full stop). Given both gates pass, the cell check emits
{FAIL, UNCHECKABLE_HERE, PASS} and the receipt verdict is:

| gates | cell check | receipt verdict token |
|---|---|---|
| any fail | — | `FAIL` |
| pass | FAIL | `FAIL` |
| pass | UNCHECKABLE_HERE | `PROVENANCE_ONLY` (sig+binding held; rule not re-derived here) |
| pass | (no witness block at all) | plain `aperture_verify` outcome; this spec adds nothing |
| pass | PASS | the **cell-specific token**: `REGISTRY_MEMBERSHIP_OK` \| `CODE_EXEC_OK` \| `MATH_TIER_D_OK` \| `MATH_TIER_S_OK` |

**Per-cell tokens, not a shared `WITNESS_OK` (rev B — closes the provenance→truth slide).** A
single top token flattened curation-membership into the same signal as decidable math truth — and
the weakest cell (registry: residual trust = the entire curated fact set) wore the strongest
badge. There is deliberately **no** bare `WITNESS_OK` in this spec. Every PASS token names the
property established, and the report carries a machine-readable `establishes` field (§D.2). A
consumer can read, from the token alone, WHICH property held.

**Composition (multi-receipt trails, multi-cell composites).** Tiers for composition:
`0 FAIL < 1 UNCHECKABLE_HERE < 2 PROVENANCE_ONLY < 3 CELL_PASS`, with `MATH_TIER_S_OK` capped at
an explicit sub-tier below the other PASS tokens. A composite reports (a) the MEET (minimum tier)
as its headline, and (b) the full list of per-component tokens — **a composite MUST NOT collapse
distinct `establishes` properties into one token.** "Any-of" pipelines take the join of
alternatives, each alternative being the meet of its own components. Tier-S components cap any
composite at the Tier-S sub-tier.

### D.2 Report schema

```json
{
  "ok": true,
  "verdict": "REGISTRY_MEMBERSHIP_OK",
  "tier": 3,
  "cell": "grounded_registry",
  "establishes": "cited spans equal a registry fact's field bytes; fact is in the operator's published registry at the signed epoch",
  "residual_trust": "the operator's self-signed registry root — curation is trusted, not proven",
  "unwitnessed_byte_ranges": [[0, 104], [130, 512]],
  "reasons": [],
  "witness_sha256": "<hex>",
  "sandbox": null,
  "versions": {"python": "3.11.9", "sympy": null},
  "checker": {"id": "aperture_witness.check_registry", "version": "1.0.0",
               "selftest_sha256": "<hex of the tamper-vector suite this build passed>"},
  "footer": "provenance + rule, not truth — this proves what was served and that its cell's rule re-derives offline, not that the answer is correct."
}
```

`residual_trust` is per-cell and mandatory (§E carries the three texts). `checker` is the
**in-band attestation** (§D.3): it names which checker's soundness the verdict is trusting.

### D.3 The soundness contract, honestly stated

The previous draft claimed "meet and join are sound **iff** every component checker is
individually sound." Two corrections:

1. **It is not an iff.** Component soundness is SUFFICIENT for composite soundness; one unsound
   component on a selected path is SUFFICIENT to break that composite. (An unsound alternative
   never selected by a join breaks nothing.)
2. **The algebra does not enforce it and cannot detect a violation.** At runtime, a PASS from an
   unsound-but-pinned checker (a Merkle verifier that accepts a bad path, an unsandboxed exec, a
   buggy SymPy identity) is byte-identical to a sound PASS. Composite soundness is an
   **out-of-band conjunction** — the release gate plus per-checker tamper-vector conformance —
   NOT a property the meet/join provides. The lattice propagates verdicts; it does not validate
   checkers. The in-band `checker` attestation (id + version + selftest hash) exists so a
   composite can at least NAME which checker's soundness each verdict rests on; naming trust is
   not removing it.

Release gates, restated as MUSTs: (1) each checker MUST pass its own tamper vectors (§F) before
it may contribute to any composite; (2) a checker that cannot run reports `UNCHECKABLE_HERE`,
which composition propagates (an unrun link is at most `PROVENANCE_ONLY` at the receipt level,
`UNCHECKABLE_HERE` in a trail); (3) a new cell type ships its tamper vectors **in the same
change**, or it is treated as unsound and may not be pinned; (4) `grounded_registry` may not be
pinned into a released verifier until the distinct registry-root key AND an external co-signature
on SRRs exist (§A.1.3) — until then it is selftest/mock only.

This is the arena contract (an LP dual certificate is worthless if the dual-feasibility checker
has a sign bug), stated as a release gate — and stated as a release gate, not miscredited to the
algebra.

---

## E. The honest boundary — what a witness proves, and what it never proves

Continuing the base SPEC §1 discipline: a verifier that implies "witness valid ⇒ answer true" is
**non-conformant**. A green per-cell PASS proves exactly two things:

1. **Provenance** — the receipt was signed by a pinned Aperture key and not altered by one byte,
   and the presented witness envelope (claim included) is byte-identical to the one signed into it.
2. **Rule-fired** — the fixed rule of the answer's cell was re-derived on the verifier's own
   machine, under the cell's stated semantics and enforcement.

It never proves **claim-truth**, and each cell's residual is different — the report's
`residual_trust` field carries these verbatim, not just a uniform footer:

- `REGISTRY_MEMBERSHIP_OK` (**the weakest cell — say so**): proves the fact is **in our
  registry** — not that it is true in the world, and not that the answer means what the fact
  means. Residuals, all of which ship in the report: (a) curation — a wrong fact, faithfully
  registered, yields a perfectly valid witness; the SRR is `operator_self_signed` and external
  co-signature does not exist yet; (b) entailment — byte-substring equality survives negation and
  modality inversion; (c) coverage — unwitnessed bytes are unverified, and the report counts
  them. Checkpoint-consistency makes silent registry rollback detectable to THIS verifier; it
  does not detect cross-verifier equivocation and cannot make curation trustless.
- `CODE_EXEC_OK`: proves the code produces the output under enforced isolation — not that the
  code correctly formalizes the user's question. A correct execution of the wrong program is a
  valid witness. On platforms where isolation cannot be attested, there is no PASS at all.
- `MATH_TIER_D_OK`: the identity holds as mathematics under decision procedures. Residual: only
  the formalization gap — nothing proves the checked statement is the one the user needed.
- `MATH_TIER_S_OK`: rests on SymPy's simplifier **in both directions** — its FAIL is not a
  disproof AND its PASS can be false on branch-cut/assumption-dependent identities. It caps every
  composite it enters and must never be laundered into a sound-composite claim.
- The **statistical layer is complemented, not replaced**: the multicell CP bound remains the only
  honest statement about *how often rule-fired diverges from correct* on the served distribution.
  Witness = exact per-answer provenance + rule; CP bound = population rate of the rule being the
  wrong rule (population-marginal, per-answer void under shift). The receipt carries both; their
  semantics MUST NOT be conflated in any report string.

The mandatory report footer, verbatim in every report:
`provenance + rule, not truth — this proves what was served and that its cell's rule re-derives
offline, not that the answer is correct.`

---

## F. Conformance (base SPEC §4 style)

An implementation is conformant if, for the vectors in `aperture_witness.py selftest` (throwaway
keys, throwaway checkpoint, no network):

**Common / binding**
- a genuine witnessed receipt verifies end-to-end to its cell's PASS token
  (`REGISTRY_MEMBERSHIP_OK` / `CODE_EXEC_OK` / `MATH_TIER_D_OK`);
- a one-byte tamper in the receipt body, the witness `witness` object, **the witness `claim`
  object (asserted_values)**, `answer_sha256`, `emitted_at`, or the answer is rejected;
- a valid witness presented with a different valid receipt (answer-hash mismatch) is rejected;
- a receipt whose signed `witness_sha256` does not match the presented envelope is rejected;
- a witnessed receipt missing the signed verbatim `answer` field is rejected
  (`missing_answer_field`);
- a fetched witness that differs from its signed hash is rejected (lying-mirror vector);
- a non-ASCII answer with multi-byte characters round-trips spans correctly (byte-offset vector).

**grounded_registry**
- a genuine fact + audit path + consistency path under a signed root verifies, for a
  non-power-of-two registry size;
- a forged fact, a truncated/reordered audit path, a wrong `leaf_index`, a root swapped for a
  different epoch's root, and a **validly-signed SRR that fails consistency against the pinned
  checkpoint (rollback/equivocation vector)** all fail;
- an SRR signed by a valid-but-unpinned key, and an SRR signed by a **receipt** key
  (key-role-crossover), are rejected as forgeries;
- a token trace claiming a span the fact does not contain, an overlapping/out-of-range/mid-byte
  span, or an unknown `norm` is rejected; `"numeric"` equates `0.5` with `0.50` and rejects
  `0.5` vs `0.499`; a numeric trace against a unit-bearing fact without the unit binding is
  rejected (`6.626e-34 kg` vs `J·s` unit-mismatch vector);
- every registry report carries `unwitnessed_byte_ranges` and the `operator_self_signed`
  residual line.

**code_verified**
- genuine code reproduces `expected_stdout` byte-exactly and verifies; one-byte tampers in code
  or `expected_stdout` fail; timeout fails; exit-code mismatch fails; `timeout_s > 60` fails;
- a **network-fetch vector** (code that opens a socket / urllib to obtain its output) is rejected
  by the static gate AND, under the OS sandbox, fails at runtime; with no attestable sandbox the
  verdict is `UNCHECKABLE_HERE` — the selftest asserts no PASS is ever produced unsandboxed;
- a nondeterminism vector (`time.time()`) is refused by the reference emitter (double-run) and
  rejected by the verifier's static gate;
- witness code cannot read the verifier's environment (empty-env assertion) and a cross-version
  stdout divergence produces a FAIL whose reason names both interpreter versions.

**math_verified**
- a genuine Tier-D derivation (including a `verify_antiderivative` certificate) verifies as
  `MATH_TIER_D_OK`; a Tier-S derivation verifies as `MATH_TIER_S_OK` (distinct token);
- a false identity, a tampered step, a wrong antiderivative (diff-check catches it), an
  expression with a non-whitelisted NAME token, attribute access, or dunder is rejected **by the
  pre-parse token gate** (asserted to fire before `parse_expr`);
- with SymPy absent, the verdict is `UNCHECKABLE_HERE` — never PASS.

**Verdict structure / boundary**
- gates-fail ⇒ `FAIL` regardless of cell result; cell `UNCHECKABLE_HERE` with green gates ⇒
  `PROVENANCE_ONLY`; a composite over {PASS, PASS, UNCHECKABLE_HERE} headlines
  `UNCHECKABLE_HERE`-tier and lists all component tokens; over {PASS, FAIL, PASS} headlines
  `FAIL`; a composite containing `MATH_TIER_S_OK` never headlines above the Tier-S sub-tier;
  no composite emits a single collapsed token for mixed `establishes` properties;
- every report contains the §E footer **verbatim**; the string-hygiene check is scoped to
  AFFIRMATIVE claims: no report string may assert "true", "correct", or "accurate" as a property
  the witness ESTABLISHED (the footer's negation "…not that the answer is correct" is REQUIRED,
  not banned — the selftest checks footer-presence and affirmative-claim-absence separately,
  never a raw substring scan);
- every report carries `establishes`, `residual_trust`, and the `checker` attestation block.

**Reference-emitter round trip**
- `witness_emitter.py` against the mock registry emits witnesses for all three cells that the
  verifier passes, refuses to emit on each cell's tamper vector (self-verify-before-sign), and
  demotes a mock answer whose alignment fails (emit-or-demote vector).
