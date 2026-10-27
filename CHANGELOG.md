# Changelog

Numbers quoted here come from the committed certificates (`nulltest_grid.json`,
`nulltest_parity.json`, `nulltest_audit_results.json`), which carry the sha256 of the tool that
produced them. What is listed under *not done* is as much a part of the record as what shipped.

## 0.6.0 — 2026-10-26 — nulltest paired mode

`nulltest.py` 1.4 is byte-identical: its sha256 does not change, so every committed certificate
(`nulltest_grid.json`, `nulltest_parity.json`, `surface_baseline_null.json`) and every published
receipt still describes the code beside it, and `--check` on an old receipt replays as before.
Paired mode was first written as a `--pair-key` flag inside `nulltest.py`; that would have changed
the tool hash and failed the `certificates` gate, so it ships as its own module. Old receipts'
verdicts are unaffected either way: the unpaired path was never touched.

**Instrument**
- `nulltest_paired.py` (also `honest paired FILE --pair-key F`): for matched pairs (one label-0, one
  label-1 per pair key; under `--explode` use `--pair-key _row`). Statistic per probe and stratum:
  A*_pair = max(w, 1-w), w = share of pairs where the label-1 member outscores the label-0 member
  (ties 1/2). Folds grouped by pair. Null: each pair's labels swapped with probability 1/2, every
  fitted probe refit. Westfall-Young min-p over the family, same pool rule and permutation floor
  rule as v1.4. Pairs that are not exactly 1-vs-1 are dropped and counted.
- 9 tests (`tests/test_nulltest_paired.py`), including one that fails if the flag is ever moved into
  `nulltest.py`.

**Evidence** (runs on the Judging-by-the-Cover binary TruthfulQA sets, arXiv 2609.13003, stratified
by TruthfulQA category)
- 790 pairs: DISQUALIFIED, words A*_pair 0.825, adjusted p 0.0038 over 226 cells.
- 476 pairs (the paper's cleaned subset): DISQUALIFIED, lexicality A*_pair 0.762, adjusted p 0.0027
  over 223 cells. Unpaired nulltest 1.4 had given this set a borderline PASS (length A* 0.551,
  adjusted p 0.0649).
- Calibration on the real 476 texts with labels coin-flipped inside each pair (null true by
  construction): 5 of 100 rejections at alpha 0.05, 200 permutations each.

**Known CI red, not caused by this change**: the `pip install .` job has failed on main since
PR #9, because `python -m nulltest` exits 1 on a DISQUALIFIED verdict by design and the step runs
under `bash -e`. The fix is a workflow edit, which needs a token with `workflow` scope.

**Not done**: no JS port of paired mode, so the browser checker cannot run it; no power grid for
paired mode yet.

## 0.5.0 — 2026-10-25 — surface_match: repair, verified held-out

`nulltest.py` 1.4 and `surface_baseline.py` are unchanged; their certificates still match.

**Instrument**
- `surface_match.py` (also `honest match FILE --out F`): keeps a surface-matched subset of a
  DISQUALIFIED set by one-to-one caliper matching on the surface logit inside each stratum,
  repeated until the surface classifier cannot separate the kept items. The matching rule is learned
  on one half and applied to the other, both ways; `nulltest` and the baseline are re-run on the
  held-out halves, and the set counts as REPAIRED only when both halves and the full matched set
  pass. Four statuses: repaired, not repaired, too small to verify, nothing to repair.
- Stop rule uses the SIGNED out-of-fold AUROC of the combiner, because a combiner fitted to noise
  generalises below one half and a rectified AUROC would read that as signal and keep matching.
- A first version balanced classes inside quantile bins by random thinning, then by nearest
  probability; both left residual separation or introduced it. Caliper matching replaced them.

**Evidence** (`matched/surface_match_results.json`, 19 rows)
- Repaired: prominence-people-v1 (76 of 168), geometry-of-truth/cities (512 of 1,496),
  truth-is-universal/facts (442 of 561), apollo/roleplaying (142 of 287).
- Seven rows pass in-sample and fail held-out (all four HaluEval sets, RAGTruth, Bielik,
  hallucinated-references): the double-dipping the two-number receipt exists to expose.
- verifydomain-claims-147 and calibration-battery-v1 are almost entirely surface (26 and 6 items
  survive); grounding-coordinate keeps all 162 and stays DISQUALIFIED because its artifact is one
  cell in a 28-item stratum the pooled classifier does not see; TruthfulQA fails both halves.
  Apollo's insider-trading-report, insider-trading-doubledown and sandbagging are almost entirely
  surface: 8, 26 and 10 items survive matching.
- Matched subsets of our own batteries committed under `matched/`; third-party subsets not
  redistributed. 63 tests after the merge with 0.4.1.

**Not done**
- No browser port of the baseline or the repair. Still no PyPI release, no outside user, no
  Actions run.
## 0.4.1 — 2026-10-25 — deception-probe datasets in the audit

`nulltest.py` 1.4 and `surface_baseline.py` are unchanged; their certificates still match.

**Harness** (`nulltest_audit.py`)
- `zip`: rows holding parallel lists (sampled responses and their grader verdicts) become one item
  per entry. Rows whose lists differ in length are skipped, not truncated.
- Dotted field paths (`metadata.report_label`) for corpora that nest their fields.
- `label_map`: label value to 0/1; any other value drops the item (a grader's `ambiguous`, a middle
  grade), and the row records `dropped`.
- Two tests (60 in all).

**Evidence** (`nulltest_audit_results.json`, `surface_baseline_results.json`, 24 rows)
- Four evaluation sets from Goldowsky-Dill et al., *Detecting Strategic Deception Using Linear
  Probes* (Apollo Research), read from the authors' repository with their own label rules. All four
  DISQUALIFIED. Surface AUROC against the paper's probe AUROC: insider-trading report 0.999 vs
  0.999; sandbagging 1.000 vs 0.983 (407 of 420 deceptive responses carry the model's own
  `<step1-results>sandbag` tag); doubling down 0.980 vs 0.974; roleplaying 0.713 vs 0.964.
- The table reads 19 of 24 DISQUALIFIED, 5 of them ours; three rows sit at A* 1.000, two of them ours.

**Not done**
- AI Liar, the paper's fifth set, is not in the table: several sampled responses per prompt would be
  treated as independent items by the folds and the null. Grouped folds first.
- The eval-awareness sets (EvalDetectBench) are on Hugging Face, which this build machine cannot reach.

## 0.4.0 — 2026-10-25 — surface_baseline: the number to beat

`nulltest.py` 1.4 is unchanged; its certificates still match. New tool, new certificates.

**Instrument**
- `surface_baseline.py` (also `honest baseline FILE`): held-out accuracy of the six probes together,
  nested five-fold on nulltest's fold assignment, ridge logistic combiner, Clopper–Pearson interval,
  balanced accuracy, AUROC, majority rate, stratum-prior accuracy, per-probe held-out AUROC. Stdlib
  only. `--audit` scores every manifest target through `nulltest_audit.load_items`; `--selftest`
  reports what it says on no-signal batteries at four sizes.
- The stratum label rate is reported beside the baseline, not fed to the combiner: a first version
  fed it in and scored a small battery balanced within each stratum at 40% accuracy, because the
  training fold's rate is anti-correlated with the held-out label there. A floor that can sit below
  chance by construction is not a floor.
- `render_audit.py` shows the baseline on the table (sha-matched to the row); CI gates
  `surface_baseline_null.json` against the shas of `surface_baseline.py` and `nulltest.py`.

**Evidence** (`surface_baseline_results.json`, 20 rows)
- HaluEval QA by answer alone: 95.4% [95.1, 95.7] against a 50% majority. HaluEval summarization
  87.2%, dialogue 78.2%. TruthfulQA reference answers 72.1% against 53.9%. HaluEval general: no
  accuracy gain over an 81.9% prior, AUROC 0.695. RAGTruth 70.8% against 57.2%, almost exactly the
  task prior (70.1%). Ours: calibration-battery-v1 92.3%, verifydomain-claims-147 88.4%,
  photon-entities 82.5%, prominence-people-v1 61.9%; the other five at the prior.
- Null (`surface_baseline_null.json`): gain over majority on no-signal batteries −2.7 points mean
  at n=162 (range −11.1 to +7.4), −0.5 at n=702, −1.5 at n=1496. Conservative by construction.
- 58 tests.

**Not done**
- No browser port of the baseline. Still no PyPI release, no outside user, no Actions run.

## 0.3.1 — 2026-10-24 — audit harness: joins, and "what carries it" on the table

The instrument (`nulltest.py` 1.4) is unchanged; every certificate's tool sha256 still matches.

**Audit harness**
- `nulltest_audit.py` manifest entries may carry a `join`: a companion file, a key and the fields
  to copy onto each item before the mapping, so a corpus whose stratum lives in a second file can
  be stratified without editing the corpus. The row records the join URL, key, fields and how many
  items had no companion row. `load_items(spec)` is now the one pipeline (fetch, parse, join,
  normalise, subsample) that the table, the dossier and the tests all call.
- `nulltest_dossier.py` also writes `nulltest_dossier_summary.json`: per row, the probes that
  separate, the worst explain cell's reading and the dossier anchor, stamped with the corpus
  sha256. `render_audit.py` reads it for a "what carries it" column on the served table, linked
  into `AUDIT_DOSSIER.md`, and will not show a summary rendered from different corpus bytes than
  the row it sits beside.

**Evidence**
- `ragtruth/responses` re-scored stratified by task type (joined from `source_info.jsonl`, 0 of
  17,790 unmatched): DISQUALIFIED stands, worst cell words/Data2txt A* 0.788, adjusted p 0.0112
  over 18 cells at 800 permutations, 9 of 18 cells separate. The earlier same-day scoring without
  the join (lexicality/_all A* 0.742 over 6 cells) is kept in `audit_notes.json` as the record of
  the change. The table still reads 15 of 20 DISQUALIFIED, 5 of them ours.
- 53 tests (two new, for the join).

**Not done**
- Still no PyPI release, no outside user, and no GitHub Actions run has ever started on this
  repository.

## 0.3.0 — 2026-10-24 — nulltest 1.4 (correction)

A statistics review of 1.3 on the day it shipped found that the permutation null was not
stratified, that the seventh probe could never score, and that Westfall–Young was biased at the
permutation floor. All three are fixed; the verdicts that changed are listed under *Evidence* and
in `audit_notes.json` as CORRECTION entries.

**Instrument**
- Stratified null: labels are shuffled within each stratum. No-signal batteries with 85/15 label
  rates false-fired 44% of the time under 1.3 (120 simulated batteries); 3.0% [1.7%, 4.9%] (15 of 500) now.
- Six probes: the `stratum` probe is removed (degenerate in every per-stratum cell by construction;
  it never produced a cell). The label rate per stratum is printed as a description.
- Westfall–Young: observed and permuted statistics are p-valued from the same pool; the
  permutation count is raised to `ceil(2(K+1)/alpha)` (hundreds) when `--n-perm` cannot resolve
  alpha for the family, and the report records `n_perm_requested` and `n_perm`.
- Worst-cell ties broken by effect `(A*−0.5)·√n`; BORDERLINE two-sided (alpha/2 ≤ adj p < 2·alpha).
- Receipts record `positive_label`; `--check` honours it and accepts `honest null` output.
- Loader: `is_true`/`truth` sniffed before `type`/`kind`; `--explode` names a field no row has;
  file-not-found, malformed JSON and empty files are sentences, not tracebacks; `--positive-label`
  is case-insensitive; `--stratum-key none` disables a sniffed stratum; the report opens with rows
  read and dropped, coerced texts and the raw value that became label-1 (`data` in the receipt).
- `--explain` shows one cell per probe before the rest (8 cells); large files print progress.
- GitHub Action: `args:` accepts quoted arguments.
- Browser engine mirrors all of the above; parity measured bit-identical on 270 cells across 14
  batteries, including astral, CJK, whitespace, quoted-CSV and small-cell fixtures (`parity/`).

**Evidence**
- Grid: 10 rows × 500 seeds (three new constructions: 85/15 label rates, 36 cells, strata of 20).
  Worst row 4.8%, pooled 4.0% (201 of 5,000); every interval under the 10% ceiling; the 85/15 row 3.0% [1.7%, 4.9%] (15 of 500).
- Audit: 17 rows re-scored; 16 verdicts unchanged. `grounding-coordinate` (ours) moves from
  BORDERLINE PASS (adj p 0.057) to BORDERLINE DISQUALIFIED (0.037), same cell, same A* 0.855.
  TruthfulQA's worst cell is now words/Law A* 0.857 (63 of 222 cells separate; tie-break by effect).
  `truth-is-universal/facts` now BORDERLINE. 12 of 17 fail, 5 of the 12 ours; two rows at A* 1.000, both ours; 15 of 20 after the three rows below.
- Audit widened to 20 rows: HaluEval summarization (A* 0.923) and HaluEval general (A* 0.690,
  the same-process human-annotated set; annotations track the task type) and RAGTruth
  (A* 0.742, length and task vocabulary). `AUDIT_DOSSIER.md`: every row's full report and feature tables, generated.
- 51 unit tests.

**Not done**
- No PyPI release; no outside user; the GitHub Actions runner on this repository has not started
  a run since the workflow was added, so CI has not executed the eight gates remotely.

## 0.2.0 — 2026-10-24 — nulltest 1.3

**Instrument**
- Seven probes (was four): `words`, `alpha_first`, `alpha_last` added. The alphabet probes catch
  `prominence-people-v1`, which passed v1.2 and was broken anyway (A* 0.661 via surname initial).
- Prepared naive-Bayes core with bit-identical scores (we measured about 13× on one battery;
  no timing is committed). `--jobs N` for parallel permutations; parallel equals serial.
- Input: JSON, JSONL, CSV, TSV; `--text-key a,b`; labels never guessed (`--positive-label`).
- `--explode FIELD=LABEL,... --split SEP` for question-answer corpora.
- `--explain`: WHY section naming the features that carry each separating cell (in-sample, never
  used for the verdict). Also in the browser engine; the parity harness compares the WHY tables.
- BORDERLINE flag within 2× of alpha. `--measure-power` re-derives the MDE table.
- Every `--json` report carries `battery_sha256` and `tool_sha256`; `--check RECEIPT` replays a
  published verdict against the bytes and says REPLAYS or DIVERGES.

**Evidence**
- Calibrated at the shipped 600 permutations, 7 grid rows × 500 seeds: worst row 5.0%, pooled 4.6%.
- Audit table 17 rows (was 14): TruthfulQA reference answers, HaluEval QA and HaluEval dialogue
  added and all three DISQUALIFIED by answer alone. 11 of 17 fail, 4 of the 11 ours.
  `grounding-coordinate` moved from DISQUALIFIED to BORDERLINE PASS when the family widened.

**Toolbox**
- `honest null` takes the full flag set; `honest check` replays a receipt; `honest audit` runs the
  table from a checkout.
- GitHub Action (`uses: techno-optimist/honestytools@main`) runs the test in a benchmark author's
  own CI, writes the receipt, and puts WHY in the job summary.
- 42 stdlib tests; CI jobs: certificates, batteries, tests, parity, selftest, read-only, install,
  action.
- CONTRIBUTING, issue templates for adding a benchmark and reporting a verdict, this changelog.
- README rewritten; CHARTER amended with what the evidence supports.

**Not done**
- Not on PyPI. `pyproject.toml` is ready and `publish.yml` publishes on a GitHub Release once a
  PyPI pending publisher exists.
- No outside user has run the tool on their own benchmark.
- The wheel does not bundle the calibrated grid; `honest fpr` declines to quote a rate it cannot
  verify against the installed code.
- `pyproject.toml` Repository URL fixed (pointed at a repository that did not exist).

## 0.1.1 and earlier

Phase 0 skeleton: the tool contract, `honest null`, nulltest v1.2 with four probes, the audit
harness, the claim gate and the parity harness. See the git history.
