# nulltest — does this benchmark measure what it says, or just how the text looks?

A truthfulness, hallucination or honesty benchmark labels its items (real vs. invented, true vs.
false, grounded vs. hallucinated) and reports how well a model tells them apart. If a small
classifier that **never sees a model**, only the surface form of the item text, can recover those
labels, then part of every score on that benchmark was spelling, length, template or sampling
order, not the ability the benchmark claims to measure.

`nulltest` measures how much of a benchmark's label signal is recoverable from surface form alone,
and says PASS, DISQUALIFIED or NOT_SCORABLE. It scores no model. It needs no GPU, no network and no
API key, and nothing from whoever built the benchmark. One Python file, standard library only, and
a JavaScript port measured bit-identical to it on 92 cells across six batteries.

```bash
git clone https://github.com/techno-optimist/honestytools && cd honestytools
python3 nulltest.py your_benchmark.json
python3 nulltest.py your_benchmark.csv --text-key statement --label-key label --positive-label 0
python3 nulltest.py your_benchmark.json --json              # machine-readable
python3 nulltest.py your_benchmark.json --jobs 8            # permutations in parallel
python3 nulltest.py your_benchmark.json --explain           # which strings carry the label
python3 nulltest.py qa_data.json --explode right_answer=0,hallucinated_answer=1   # QA rows
python3 -m unittest discover -s tests                        # 63 tests, a few seconds
```

(`pip install honesttools` is the plan, and `pyproject.toml` is ready for it, but the package is
**not on PyPI yet**. Until it is, clone.)

## What this repository offers

| you want to | do this |
|---|---|
| test a benchmark file on your laptop | `python3 nulltest.py FILE` (JSON, JSONL, CSV, TSV; `--explode` for QA rows) |
| know *why* it failed | `--explain`, or read the WHY section every DISQUALIFIED report prints |
| test it in your own CI, every commit | the [GitHub Action](#run-it-in-your-own-ci) below, five lines of YAML |
| test it in a browser, nothing uploaded | [honesty.tools/evidence](https://www.honesty.tools/evidence) runs the same engine, proven bit-identical |
| publish a verdict someone else can check | `--json` writes a receipt with the file's sha256 and the tool's; `--check RECEIPT` replays it |
| see how published benchmarks fare | the [audit table](https://www.honesty.tools/audit), 24 rows, every one with a URL and a sha256 |
| know what carries the label in each of them | [AUDIT_DOSSIER.md](AUDIT_DOSSIER.md): every row's full report and `--explain` feature tables, generated, never typed |
| repair a benchmark the surface can score | `python3 surface_match.py FILE --out matched.json`: keeps a surface-matched subset, verified on held-out halves; see [repair](#repair-keep-the-items-the-surface-cannot-tell-apart) |
| know the number a detector has to beat | `python3 surface_baseline.py FILE`: held-out accuracy of the six probes together, against the majority rate; see [the number to beat](#the-number-to-beat) |
| put your benchmark in that table | one manifest entry; see [CONTRIBUTING](CONTRIBUTING.md) or open an [issue](https://github.com/techno-optimist/honestytools/issues/new/choose) |
| know what the tool cannot see | the measured [false-positive](#what-the-tool-says-about-itself) and power tables, and the [limits](#known-limits--read-these-before-quoting-a-verdict) |
| one entry point for the toolbox | `honest null FILE`, `honest baseline FILE`, `honest match FILE --out F`, `honest check RECEIPT FILE`, `honest audit`, `honest fpr null` |

## Who this is for

- **Authors of benchmarks** with a two-class label over text, before they publish. Thirty seconds
  tells you whether a regex could score your benchmark.
- **Reviewers and leaderboard maintainers** checking a benchmark they did not build. Every verdict
  carries the sha256 of the file it scored, so a reader can re-derive it.
- **Anyone reading a hallucination number** who wants to know how much of it the text alone
  explains.

It is **not** a model evaluator, and a team shipping a model has little use for it unless they also
build their own test sets. An earlier version of this page pitched it to that audience; the audit
table below, which is the only usage record we have, contains only benchmarks.

## Input

JSON (a list, or an object holding a list under `items`, `battery`, `data`, `rows`, `examples` or
`instances`), JSONL, CSV or TSV. The tool picks the text, label and stratum fields from the item
keys and prints which ones it chose; override them with `--text-key`, `--label-key`,
`--stratum-key`. Several text fields can be scored together: `--text-key question,answer`.

Labels may be `0/1`, booleans, or a recognised word pair (`real/fake`, `true/false`,
`faithful/hallucinated`, `correct/incorrect` and a few others). Any other pair stops the run and
asks for `--positive-label VALUE`. The tool does not guess, because a guess that happened to be
backwards would still print a confident verdict.

### Question-answer benchmarks: `--explode`

The test needs one text and one two-class label per item. Most QA benchmarks store a question
with a right answer and a wrong one on the same row. `--explode FIELD=LABEL,...` turns each named
field into its own item carrying that label; `--split ";"` separates several answers in one cell;
the row's other fields are kept, so the question's category can be the stratum. The question that
this asks of such data is: can the answer be told right from wrong **without the question**?

```bash
# HaluEval QA: {"question", "right_answer", "hallucinated_answer"} per line
python3 nulltest.py qa_data.json --explode right_answer=0,hallucinated_answer=1

# TruthfulQA: several ';'-separated answers per cell, one category per question
python3 nulltest.py TruthfulQA.csv --explode "Correct Answers=0,Incorrect Answers=1" --split ";" --stratum-key Category
```

Both are in the audit table below. A benchmark with more than two classes has to be filtered to
two first; the tool refuses to pick.

### `--explain`: which strings carry the label

A DISQUALIFIED verdict is only useful if the author can find the artifact. The report therefore
ends with a WHY section (always on a DISQUALIFIED, `--explain` to force it) that describes each
separating cell in-sample: for the fitted probes, the features most over-represented in each class
with their counts; for the score probes, the class medians in plain words ("label-1 items are
longer: median 9 vs 8 characters", "last-word initials: label-1 median 'g' vs label-0 median 'l'").
These tables are a description for finding the artifact, not the evidence for the verdict; the
verdict is held-out and permutation-calibrated, the tables are not.

### Run it in your own CI

```yaml
- uses: techno-optimist/honestytools@main
  with:
    file: data/benchmark.jsonl
    args: --text-key statement --label-key label        # any nulltest.py flags
    fail-on: DISQUALIFIED                               # or BORDERLINE, or NEVER
```

The step needs Python 3.10+ and nothing else. It writes the full report to `nulltest_report.json`,
puts the verdict and the WHY section in the job summary, exposes `verdict` and `headline` as step
outputs, and fails the job on a DISQUALIFIED (configurable). Our own CI runs the action on our own
batteries and checks that it fails where it should.

### Receipts: a verdict someone else can check

Every `--json` report carries `battery_sha256` (the bytes scored), `tool_sha256` (the code that
scored them), the keys it read and the permutation count. Publish that file next to your benchmark
and anyone can replay it:

```bash
python3 nulltest.py benchmark.jsonl --json > nulltest_receipt.json
python3 nulltest.py benchmark.jsonl --check nulltest_receipt.json
#   REPLAYS — the same verdict, the same worst cell, the same numbers, from the same bytes.
```

`--check` refuses a receipt that names different bytes, says so when the tool version differs,
and exits non-zero on DIVERGES. It also accepts the JSON that `honest null` writes. Every number
this page quotes comes from a committed file or says where it came from.

### What the report says before the verdict

The text report opens with what the loader did, because a silent change of corpus is a silent
change of result: how many rows it read and dropped (a row without the text or label field is
dropped and counted), how many texts were empty or coerced from numbers, and which raw label
value became **label-1**. Every WHY line is phrased in terms of label-1, so that line is the key to
them. Labels are mapped without a flag only for `0/1`, booleans, and these word pairs (case-folded):
fake/real, fabricated/genuine, false/true, hallucinated/faithful, incorrect/correct, wrong/right,
no/yes (where "no" is label-1), unfaithful/grounded. Anything else stops and asks for
`--positive-label VALUE`, which is matched case-insensitively and recorded in the receipt. When a
stratum field is sniffed from the file and you do not want one, pass `--stratum-key none`.

### Exit codes

| command | 0 | 1 | 2 |
|---|---|---|---|
| `nulltest.py BATTERY` (text or `--json`) | PASS | DISQUALIFIED or NOT_SCORABLE | usage error, unreadable file, undecidable labels |
| `nulltest.py BATTERY --check RECEIPT` | REPLAYS | DIVERGES | receipt names other bytes or cannot be replayed |
| `nulltest.py --selftest` | within the ceiling | FAIL | INDISTINGUISHABLE (25 seeds cannot resolve 5% vs 10%; expected) |
| `honest null BATTERY` | a verdict a pipeline may rely on: PASS with a matching certificate, or DISQUALIFIED | anything else | usage |
| `nulltest_action.sh` | per `fail-on` | the verdict named by `fail-on` | bad input |

`python3 -m nulltest` is the same program as `python3 nulltest.py` and is the form the wheel
offers. Large files print a permutation counter to stderr while they run (a 60,000-item file takes
about a quarter of an hour on four cores; `--jobs` is a no-op on platforms without `fork`).

## The six surface probes

| probe | what it asks |
|---|---|
| `lexicality` | character 3–5-grams, naive Bayes: does the string *look* like one class? |
| `words` | word unigrams and bigrams, naive Bayes: does the wording or the template leak the label? |
| `length` | character and word count: is one class shorter or longer? |
| `shape` | digit / ALL-CAPS / punctuation signature: are reals ID-shaped and fakes word-shaped? |
| `alpha_first` | alphabet position of the first word's initial: was one class taken from the top of a sorted list? |
| `alpha_last` | same for the last word's initial: lists sorted by surname |

The fitted probes are scored on held-out folds, so an item never contributes to the statistics
that score it. Each probe is **two-sided** (`A* = max(AUROC, 1−AUROC)`, because an inverted
classifier is still a classifier), scored **per stratum with the worst cell reported** rather than
the mean, **calibrated against label shuffles of the same data, shuffled within each stratum, with the probes
refit inside every shuffle**, and **corrected across every cell tested** (Westfall–Young min-p). The
PASS/DISQUALIFIED line is drawn at a family-adjusted p of 0.05; a verdict whose worst cell lands
within a factor of two of that line on either side is marked **BORDERLINE** in the report, because
the number of cells in the family can move such a result on its own.

The null is stratified. Labels are shuffled only within a stratum, so a stratum whose label rate
differs from its neighbours' (80% label-1 here, 20% there) is conditioned on, not tested: the
question is whether the text separates the classes *beyond* what the category already tells you.
The report prints the label rate per stratum as a description. v1.3 shuffled across strata and
carried a seventh "stratum" probe meant to catch exactly that imbalance; the probe could never
score (a per-stratum rate is constant inside a per-stratum cell) and the unstratified null
false-fired on 44% of no-signal batteries with 85/15 label rates. See *Correction* below.

The permutation count is raised automatically when it cannot resolve alpha for the family size
(to `ceil(2(K+1)/alpha)`, rounded up to the hundred, so 600 for 12 cells and 8,900 for 222); the
report records both the count requested and the count used.

The `words`, `alpha_first` and `alpha_last` probes are new in v1.3. The alphabet probes exist
because our own `prominence-people-v1` battery passed the four probes of v1.2 and was broken
anyway: its builder took the alphabetically-first 200 members of each Wikipedia category, and
biography categories sort by surname. The surname initial alone recovers its labels at A* 0.661.
Alphabetical sampling is a common way to build a list, so the probe is general. It now catches
that battery.

## Where the idea comes from

This is the hypothesis-only / partial-input baseline of the NLP artifact literature (Gururangan et
al. 2018, Poliak et al. 2018) and the artifact filtering of AFLite (Le Bras et al. 2020), applied to
two-class text benchmarks. The idea is not new. What this tool adds is the discipline around it:
two-sided scoring, worst cell not mean, refit inside every permutation, family-wise correction, a
measured false-positive rate, a reproducible power table, and a browser engine measured
bit-identical to the command line on 92 cells. Cite it as a careful packaging of a known test.

## What the tool says about itself

**Measured false-positive rate**, v1.4, 500 seeds per grid row at the shipped permutation count
(600, raised automatically where the family needs more), exact 95% intervals. Reproduce with
`python3 nulltest.py --calibrate --jobs 4 --emit-grid /tmp/grid.json` (about 26 minutes on our
4-core machine; not timed elsewhere):

| construction | rate | 95% CI |
|---|---|---|
| planted-clean as shipped (alternating, 1 stratum) | 4.8% | [3.1%, 7.1%] |
| coin-flip labels, 1 stratum | 4.4% | [2.8%, 6.6%] |
| coin-flip, 2 strata | 4.6% | [2.9%, 6.8%] |
| coin-flip, 4 strata | 4.4% | [2.8%, 6.6%] |
| coin-flip, 6 strata | 2.4% | [1.2%, 4.2%] |
| coin-flip, word-shaped text (all 6 probes live), 1 stratum | 4.4% | [2.8%, 6.6%] |
| coin-flip, word-shaped text (all 6 probes live), 4 strata | 3.8% | [2.3%, 5.9%] |
| coin-flip, word-shaped, 2 strata with 85/15 label rates | 3.0% | [1.7%, 4.9%] |
| coin-flip, word-shaped, 6 strata (36 cells) | 4.4% | [2.8%, 6.6%] |
| coin-flip, word-shaped, 3 strata of 20 items | 4.0% | [2.5%, 6.1%] |

Every upper bound is below the 10% ceiling; the pooled rate is 4.0% over 5,000 no-signal batteries (201 false fires), worst row 4.8%. The three rows added in v1.4 are the constructions v1.3 was never tested on: unequal
label rates per stratum (the construction v1.3 false-fired on 44% of the time), a 36-cell family,
and strata of 20 items. An earlier v1.3 run at 200 permutations, not committed, put a 24-cell
family at 8.4% [6.1%, 11.2%]; the automatic raise of the permutation count exists so that no
shipped setting sits at that floor.

**Measured power**, v1.4. A single character appended to a fraction of the label-1 half of a clean
synthetic battery; the smallest fraction caught in at least 3 of 4 seeds. Reproduce with
`python3 nulltest.py --measure-power --jobs 4`:

| n | smallest planted coverage caught |
|---|---|
| 156 | 60% |
| 324 | 60% |
| 702 | 30% |
| 1496 | 15% |

Unchanged from v1.3: the same four rows were re-measured under v1.4 and agree.

A PASS at n=156 therefore says only that no artifact covering 60% or more of one class was found.
The POWER line in every report quotes this table for the battery's n.

**`--selftest` is a smoke test, not a certificate.** At 25 seeds it usually returns
INDISTINGUISHABLE, which is the honest answer: a 25-seed point estimate of a 5% rate compared
against a 10% ceiling fails a perfectly calibrated tool about half the time. `--selftest` is
read-only. `--calibrate --emit-grid PATH` writes the certificate, refuses to write into the tool's
own directory, and stamps the tool's sha256 into it so a reader can tell whether a certificate
describes the code in front of them. CI checks that stamp on every commit.

## Your benchmark failed. Now what?

A DISQUALIFIED verdict means a reader who never sees the question or the model can tell your two
classes apart from the text alone. It does not mean the benchmark is worthless; it means part of
every score on it is that. What to do, by what the WHY section names:

- **`length`** (one class is longer): match the length distributions. Trim or regenerate the longer
  class, or sample pairs matched on length, and re-run. HaluEval's hallucinated answers are nearly
  five times longer than its reference answers; that alone scores A* 0.97.
- **`words` or `lexicality`** (a word, a phrase, a spelling pattern): look at the feature table.
  Template leaks ("I have no comment", "it is not true that") mean the wrong answers were written
  by a different process than the right ones. Regenerate both classes with the same process, or
  paraphrase the leaking phrases away, then re-run; the probe will find the next-biggest leak, which
  is the point.
- **`shape`** (digits, capitals, punctuation): usually an ID-shaped class against a word-shaped
  class. Normalise the formatting or drop the field that carries it.
- **`alpha_first` / `alpha_last`** (alphabet position of an initial): one class was taken from the
  top of a sorted list. Sample at random, or from the whole list, and re-run.
- **The label rate differs by category** (the `label-1 rate by stratum` lines): the test conditions
  on this and does not flag it, but a model can exploit it. Rebalance labels within each category.

Or let `surface_match.py` do the matching for you and verify it held-out; see
[repair](#repair-keep-the-items-the-surface-cannot-tell-apart). Then re-run and read the POWER line: a
PASS at n=156 says only that no artifact covering 60% of a class survived. Fixing the surface does not fix provenance; a benchmark can pass this test and
still be contaminated, circular or mislabelled.

## Known limits — read these before quoting a verdict

- **A PASS is not a certificate of quality.** It says these six probes did not recover the labels
  at this n. A weaker or a differently shaped artifact can be present and missed; the power table
  says how weak.
- **Circularity, contamination and mislabelling are invisible here.** They are provenance
  properties, not surface ones.
- **A wider family costs power.** Going from four probes to seven in v1.3 moved our own
  `grounding-coordinate` battery from DISQUALIFIED (adjusted p 0.030 over 18 cells) to PASS (0.057
  over 35 cells) with the same worst cell at the same A*. That is why BORDERLINE exists. If you
  only care about one probe, the per-cell `raw_p` is in the JSON.
- **Westfall–Young needs permutations to match the family.** With 24 cells the false-fire rate
  measured 8.4% at 200 permutations (an uncommitted v1.3 run), a discreteness effect at the
  permutation floor. Since v1.4 the tool raises the count itself to `ceil(2(K+1)/alpha)` and says
  so in the report; `--n-perm` is a floor, not a ceiling.
- **Only two classes, and no pairing.** A multi-class corpus must be filtered to two labels first.
  Question-answer rows exploded with `--explode` are scored as a pool of answers, not as pairs
  within a question; a paired, per-question comparison is not offered.
- **Non-Latin text.** The probes run on any Unicode text, but the alphabet probes drop out as
  degenerate when initials are not letters, and `words` splits on whitespace, so unspaced scripts
  give it one token per sentence. The report lists which cells were dropped as degenerate; it
  does not detect the language.
- **`A*` is not a stable effect size.** It is a fitted classifier's output and depends on n. Rows of
  different n are not comparable.
- **No external co-signer, and no outside user yet.** Every number here is our own measurement of
  our own tool, and no one outside this project has run it on their own benchmark. The charter
  names that as the test the project must pass.

## What's in here

| file | role |
|---|---|
| `nulltest.py` | the instrument. `--selftest`, `--calibrate`, `--measure-power`, `--emit-grid` |
| `nulltest.js` | the same instrument for a browser; `bash nulltest_parity.sh` (needs node) checks the two agree cell by cell. The browser engine scores flat two-class files and shows the WHY tables; `--explode` is command-line only |
| `tests/` | 63 unit tests: every probe catches the artifact it was written for, the stratified null holds on unequal label rates, the loader refuses to guess and says what it dropped, parallel equals serial, receipts replay |
| `action.yml`, `nulltest_action.sh` | the GitHub Action; the script runs without GitHub too |
| `CONTRIBUTING.md`, `CHANGELOG.md` | how to add a benchmark to the table, how to change the tool, what shipped and what did not |
| `nulltest_audit.py` | scores a manifest of published benchmarks and writes the table behind [honesty.tools/audit](https://www.honesty.tools/audit) |
| `nulltest_audit_manifest.json` | the targets: URL, exact field mapping, label polarity, and what kind of label each is |
| `nulltest_audit_results.json` | the table. Every row carries the sha256 of the bytes scored |
| `AUDIT_DOSSIER.md` | one section per row: what the corpus is, the full verdict, and the `--explain` feature tables that say what carries the label; rendered from the reports, never edited by hand |
| `nulltest_dossier.py` | renders it: scores every manifest target with `--explain` (cached per row) and writes `AUDIT_DOSSIER.md` |
| `surface_baseline.py` | the number to beat: nested five-fold held-out accuracy of the six probes combined by a ridge logistic regression, with a Clopper–Pearson interval, against the majority rate; `--audit` scores every manifest target, `--selftest` reports what it says on noise |
| `surface_match.py` | repair: one-to-one caliper matching on the surface logit inside each stratum, rule learned on one half and applied to the other both ways, `nulltest` and the baseline re-run on the held-out halves; writes the kept items plus a receipt; `--audit` repairs every DISQUALIFIED manifest row |
| `matched/surface_match_results.json` | the repair certificate for the audit table: per DISQUALIFIED row, how many items survive matching and whether the held-out halves pass; `matched/aperture_*.json` are the matched subsets of our own batteries (third-party subsets are not redistributed) |
| `surface_baseline_results.json` | that number for every audited benchmark, stamped with the corpus sha256 and the shas of `surface_baseline.py` and `nulltest.py`; `render_audit.py` shows it on the table only when the shas match the row |
| `surface_baseline_null.json` | the baseline's own null: its gain over the majority rate on no-signal batteries at four sizes (slightly below zero at small n, the protocol's pessimism); gated in CI against the tool's sha |
| `nulltest_dossier_summary.json` | the dossier's per-row summary (probes, the worst explain cell's reading, dossier anchor, corpus sha256); `render_audit.py` reads it for the table's "what carries it" column and refuses to show a summary rendered from different corpus bytes than the row |
| `nulltest_grid.json` | the false-positive certificate, sha256-stamped to the tool that produced it |
| `batteries/` | our own nine batteries, all of them in the table |
| `honest.py`, `contract.py` | the toolbox entry point (`honest null`, `honest check`, `honest audit`, `honest fpr`) and the four-method tool contract; see `CHARTER.md` |
| `render_audit.py`, `claim_gate.py`, `null_floor_reconcile.py`, `demo.sh` | the harness we run against our own site; they expect the site tree beside them and do nothing useful in a bare checkout |

`audit_cache/` is gitignored on purpose: the audit records a URL and a sha256 for each third-party
corpus, never a copy.

## Correction, 2026-10-24: what v1.4 changed and why

A statistics review of v1.3, done the same day it shipped, found three defects. They are recorded
here because the numbers above and the table below were published under v1.3 for part of a day.

1. **The null was not stratified.** Labels were shuffled across strata, so a corpus whose categories
   had different label rates produced a false DISQUALIFIED on 44% of no-signal runs (85/15 rates,
   120 simulated batteries). The v1.3 grid never contained that construction, so the certificate
   was true and incomplete. v1.4 shuffles within stratum; the same construction now false-fires at
   3.0% [1.7%, 4.9%] (15 of 500) and is a row of the committed grid.
2. **The seventh probe could not fire.** The `stratum` probe scored a per-stratum rate inside
   per-stratum cells, where it is constant, so it was dropped as degenerate on every battery. The
   README described a probe that never produced a cell. It is removed; the label rate per stratum
   is printed as a description instead.
3. **Westfall–Young at the floor.** The permuted and observed statistics were p-valued differently,
   which biased the adjusted p at wide families, and 600 permutations cannot resolve 0.05 over 222
   cells. Both are fixed; the count is now raised automatically and recorded.

Smaller: ties in the worst cell are broken by effect size rather than by order of discovery;
BORDERLINE is two-sided; receipts record `--positive-label`; the loader reports what it dropped.

**What the correction did to the audit table.** Every row was re-scored under v1.4 against the
same bytes (the sha256 of each corpus is in `nulltest_audit_results.json`). Sixteen of seventeen
verdicts are unchanged. One changed: our own `grounding-coordinate` goes from a BORDERLINE PASS
(adjusted p 0.057 over 35 cells, v1.3) to a BORDERLINE DISQUALIFIED (0.037 over the same 35 cells,
same worst cell, same A* 0.855). Under v1.2 it was DISQUALIFIED at 0.030 over 18 cells; v1.3's PASS
was the only verdict it ever got, and v1.3's null was the defective one. Two rows changed shape
without changing verdict: TruthfulQA's worst cell is now `words`/Law at A* 0.857 over n=586 (63
of 222 cells separate, was 77) because ties at the permutation floor are now broken by effect
size rather than by order of discovery, so a large cell wins over a small cell at A* 1.000; and
`truth-is-universal/facts` is now marked BORDERLINE (adjusted p 0.038). The table therefore reads
12 of 17 DISQUALIFIED, 5 of the 12 ours; two rows sit at A* 1.000 and both are ours. Three rows added later the same evening (HaluEval summarization, HaluEval general, RAGTruth) make it 15 of 20. Four rows from Apollo Research's deception-probe datasets, added 2026-10-25, make it 19 of 24; one of them, sandbagging, also sits at A* 1.000, so three rows do and two are ours.

## Repair: keep the items the surface cannot tell apart

A DISQUALIFIED verdict tells an author their set is scorable by a regular expression. `surface_match.py`
gives them a set that is not, when one exists inside the original:

```
python3 surface_match.py FILE --out matched.json [--text-key K --label-key K --stratum-key K]   # or: honest match
python3 surface_match.py --audit --out-dir matched/            # every DISQUALIFIED row of the audit table
```

It fits the surface classifier (the baseline's combiner) out-of-fold, then pairs every item of the
smaller class with its nearest unmatched item of the other class on the surface logit, inside its
stratum and within a caliper of a fifth of a standard deviation, and drops whatever finds no twin.
It repeats until the combiner can no longer separate the kept items, and writes them out unchanged,
with a receipt.

**The receipt has two numbers because one would lie.** A subset tested on the same items it was
selected with is flattered: you removed the items that made the probes fire. So the matching *rule*
(the fitted models and calipers, not the item choices) is learned on one half of the data and
applied to the other, both ways, and `nulltest` and the baseline are re-run on those held-out
halves. That is what the procedure achieves on items it never selected on. The full-data subset is
what you ship; its own in-sample verdict is printed beside the held-out ones and labelled
optimistic. The set counts as REPAIRED only when both held-out halves and the full matched set pass.

Every DISQUALIFIED row of the audit table, from `matched/surface_match_results.json`:

| benchmark | items | kept | held-out half 1 → 2 | held-out half 2 → 1 | full matched set (in-sample) | result |
|---|---|---|---|---|---|---|
| aperture/verifydomain-claims-147 | 147 | 26 (18%) | NOT_SCORABLE | NOT_SCORABLE | NOT_SCORABLE | **too small to verify** |
| aperture/grounding-coordinate | 162 | 162 (100%) | DISQUALIFIED | PASS | DISQUALIFIED | **not repaired** |
| aperture/photon-entities | 103 | 30 (29%) | NOT_SCORABLE | NOT_SCORABLE | DISQUALIFIED | **not repaired** |
| aperture/prominence-people-v1 | 168 | 76 (45%) | PASS | PASS | PASS | **REPAIRED** |
| bielik-entity-familiarity-v2 | 1440 | 172 (12%) | DISQUALIFIED | PASS | PASS | **not repaired** |
| hallucinated-references/gpt-4 | 1000 | 452 (45%) | PASS | DISQUALIFIED | PASS | **not repaired** |
| geometry-of-truth/cities | 1496 | 512 (34%) | PASS | PASS | PASS | **REPAIRED** |
| truth-is-universal/facts | 561 | 442 (79%) | PASS | PASS | PASS | **REPAIRED** |
| truthfulqa/answers | 6028 | 2446 (41%) | DISQUALIFIED | DISQUALIFIED | DISQUALIFIED | **not repaired** |
| halueval/qa | 20000 | 1346 (7%) | PASS | DISQUALIFIED | PASS | **not repaired** |
| halueval/dialogue | 20000 | 8378 (42%) | DISQUALIFIED | DISQUALIFIED | PASS | **not repaired** |
| aperture/calibration-battery-v1 | 168 | 6 (4%) | NOT_SCORABLE | NOT_SCORABLE | NOT_SCORABLE | **too small to verify** |
| halueval/summarization | 20000 | 2732 (14%) | DISQUALIFIED | DISQUALIFIED | PASS | **not repaired** |
| halueval/general | 4507 | 1462 (32%) | DISQUALIFIED | PASS | PASS | **not repaired** |
| ragtruth/responses | 8000 | 3108 (39%) | PASS | DISQUALIFIED | PASS | **not repaired** |

Three sets are repairable: `prominence-people-v1` keeps 76 of 168 items and the alphabet artifact
goes with the rest; `geometry-of-truth/cities` keeps 512 of 1,496; `truth-is-universal/facts` keeps
442 of 561. Seven rows show the trap this receipt exists for: HaluEval QA, dialogue and
summarization, HaluEval general, RAGTruth, Bielik and the hallucinated-references set each produce a
full matched set that **passes in-sample and fails held-out**. Had the tool reported only the first
number, all seven would have been called fixed. Two of ours (`verifydomain-claims-147`,
`calibration-battery-v1`) are almost entirely surface: matching leaves 26 and 6 items. They need new
items, not a subset. `grounding-coordinate` keeps everything and stays DISQUALIFIED: its artifact
is one cell in a 28-item stratum, which the pooled surface classifier does not see, so matching on
it removes nothing. TruthfulQA's 2,446 matched reference answers still fail both halves.

What this cannot do: add items; see an artifact the six probes do not; or leave what the set
measures unchanged. A matched set keeps the surface-ambiguous items and measures something narrower
than the original, and must be cited as surface-matched. A clean held-out half also fails one time
in twenty by chance, because `nulltest` is a 5% test, so a borderline held-out DISQUALIFIED should
be read with its adjusted p. The matched subsets of our own batteries are committed under
`matched/`; third-party subsets are not redistributed, and the one-line command above regenerates
them from the public corpora.

## The number to beat

`nulltest` says *whether* a surface probe separates the classes. `surface_baseline.py` says *how
much*: the held-out accuracy all six probes earn together, with no model and without reading the
question, passage or dialogue an item answers. That is the floor a detector or judge scored on the
benchmark has to clear before its number says anything about hallucination, truth or familiarity.

```
python3 surface_baseline.py FILE [--text-key K --label-key K --stratum-key K]   # or: honest baseline FILE
python3 surface_baseline.py --audit --out surface_baseline_results.json       # every row of the table
python3 surface_baseline.py --selftest --out surface_baseline_null.json       # what it says on noise
```

Nested five-fold cross-validation on nulltest's own fold assignment: inside each training fold the
naive-Bayes probes are scored out-of-fold again, a ridge logistic regression is fitted on those
scores, and the held-out fold is scored against the whole training fold's counts. No item's label
touches the score that classifies it. The report carries accuracy with a 95% Clopper–Pearson
interval, balanced accuracy, AUROC, the majority-class rate, the in-sample accuracy of knowing each
stratum's label rate, and each probe's held-out AUROC alone. Standard library only, seeded, no
browser port. It is a *lower* bound on what the surface yields: six features and a linear combiner
are not an adversary.

Every audited benchmark, from `surface_baseline_results.json` (stamped with the corpus sha256 and
the shas of `surface_baseline.py` and `nulltest.py`; the table on honesty.tools/audit shows the
number only when the shas match the row):

| benchmark | n | nulltest verdict | majority | surface baseline (95% CI) | balanced | AUROC |
|---|---|---|---|---|---|---|
| aperture/verifydomain-claims-147 | 147 | DISQUALIFIED | 75.5% | **88.4%** [82.1%, 93.1%] | 79.2% | 0.947 |
| aperture/grounding-coordinate | 162 | DISQUALIFIED (borderline) | 50.0% | **40.7%** [33.1%, 48.7%] | 40.7% | 0.373 |
| aperture/photon-entities | 103 | DISQUALIFIED | 73.8% | **82.5%** [73.8%, 89.3%] | 71.4% | 0.847 |
| aperture/prominence-people-v2 | 156 | PASS | 50.0% | **48.7%** [40.6%, 56.8%] | 48.7% | 0.480 |
| aperture/prominence-people-v1 | 168 | DISQUALIFIED | 50.0% | **61.9%** [54.1%, 69.3%] | 61.9% | 0.632 |
| aperture/postcutoff-trials-v1 | 702 | PASS | 50.0% | **45.9%** [42.1%, 49.6%] | 45.9% | 0.464 |
| bielik-entity-familiarity-v2 | 1440 | DISQUALIFIED | 83.3% | **90.0%** [88.3%, 91.5%] | 73.5% | 0.926 |
| hallucinated-references/gpt-4 | 1000 | DISQUALIFIED | 53.2% | **77.9%** [75.2%, 80.4%] | 77.8% | 0.859 |
| geometry-of-truth/cities | 1496 | DISQUALIFIED | 50.0% | **80.8%** [78.7%, 82.8%] | 80.8% | 0.871 |
| geometry-of-truth/sp_en_trans | 354 | PASS | 50.0% | **49.7%** [44.4%, 55.0%] | 49.7% | 0.484 |
| truth-is-universal/facts | 561 | DISQUALIFIED (borderline) | 50.3% | **59.0%** [54.8%, 63.1%] | 59.0% | 0.625 |
| truthfulqa/answers | 6028 | DISQUALIFIED | 53.9% | **72.1%** [70.9%, 73.2%] | 71.2% | 0.796 |
| halueval/qa | 20000 | DISQUALIFIED | 50.0% | **95.4%** [95.1%, 95.7%] | 95.4% | 0.980 |
| halueval/dialogue | 20000 | DISQUALIFIED | 50.0% | **78.2%** [77.7%, 78.8%] | 78.2% | 0.862 |
| aperture/knowledge-prominence-v1 | 324 | PASS | 50.0% | **48.8%** [43.2%, 54.4%] | 48.8% | 0.470 |
| aperture/knowledge-prominence-v2 | 296 | PASS | 50.0% | **51.3%** [45.5%, 57.2%] | 51.3% | 0.518 |
| aperture/calibration-battery-v1 | 168 | DISQUALIFIED | 50.0% | **92.3%** [87.1%, 95.8%] | 92.3% | 0.980 |
| halueval/summarization | 20000 | DISQUALIFIED | 50.0% | **87.2%** [86.7%, 87.6%] | 87.2% | 0.945 |
| halueval/general | 4507 | DISQUALIFIED | 81.9% | **81.8%** [80.7%, 83.0%] | 52.0% | 0.695 |
| ragtruth/responses | 8000 | DISQUALIFIED | 57.1% | **70.8%** [69.8%, 71.8%] | 68.9% | 0.754 |
| apollo/roleplaying | 287 | DISQUALIFIED | 70.7% | **73.5%** [68.0%, 78.5%] | 58.2% | 0.713 |
| apollo/insider-trading-report | 1005 | DISQUALIFIED | 66.6% | **99.6%** [99.0%, 99.9%] | 99.5% | 0.999 |
| apollo/insider-trading-doubledown | 655 | DISQUALIFIED | 96.3% | **97.4%** [95.9%, 98.5%] | 72.6% | 0.980 |
| apollo/sandbagging | 932 | DISQUALIFIED | 54.9% | **99.4%** [98.6%, 99.8%] | 99.4% | 1.000 |

Read each row against its prior. HaluEval QA scored by answer alone is 95% solvable from the
surface; a judge reported at 80% on it has shown nothing yet. On HaluEval general the surface earns
no accuracy at the 0.5 threshold because 82% of responses are clean, but the AUROC of 0.695 says the
ranking carries task information; read balanced accuracy and AUROC when the prior is lopsided. On
RAGTruth the surface (70.8%) is almost exactly the task prior (70.1%): a detector that knows which
task it is looking at starts there. Our own two worst batteries are 88% and 92% solvable; four of
ours sit at the prior.

**Deception probes.** The four `apollo/` rows are the evaluation sets of *Detecting Strategic
Deception Using Linear Probes* (Goldowsky-Dill et al., arXiv:2502.03407): Llama-3.3-70B's own
responses, graded honest or deceptive by the authors' model grader, read from their repository with
their label rules. The paper's probe reads the model's activations over those same response tokens.
Set its AUROC beside the surface's:

| dataset | probe AUROC (paper) | surface AUROC (no model) |
|---|---|---|
| insider trading, report | 0.999 | 0.999 |
| sandbagging | 0.983 | 1.000 |
| insider trading, doubling down | 0.974 | 0.980 |
| roleplaying | 0.964 | 0.713 |

On three of the four the response text alone matches or beats the probe. In sandbagging, 407 of the
420 deceptive responses contain the model's own tag `<step1-results>sandbag`, and every honest one
says `normal`. Roleplaying is where the probe clearly reads more than wording. This is not a claim
that the probes fail or that the paper is wrong: in these scenarios a lie is often visible in the
words, and the paper's own black-box reader did about as well as the probe. It says these sets
cannot separate "the probe reads deception" from "the probe reads wording", so a probe evaluated
on them needs the surface number beside its own. The paper's fifth set, AI Liar, is left out for
now: it holds several sampled responses per prompt, and nulltest's folds and null treat them as
independent items, which would overstate the surface.

**What it reports on noise.** Cross-validated accuracy on no-signal data sits slightly *below* the
majority rate at small n, because a combiner fitted to noise generalises a little worse than
chance. The floor is therefore conservative, and a gain inside the band below is not evidence of a
surface. From `surface_baseline_null.json` (balanced labels, six strata, random tokens):

| n | null batteries | gain over majority, mean | range | AUROC mean (min) |
|---|---|---|---|---|
| 162 | 30 | -2.7 pts | [-11.1, +7.4] | 0.484 (0.397) |
| 324 | 20 | -3.1 pts | [-8.3, +0.9] | 0.474 (0.398) |
| 702 | 12 | -0.5 pts | [-4.0, +1.9] | 0.493 (0.450) |
| 1496 | 8 | -1.5 pts | [-3.1, +0.4] | 0.491 (0.462) |

So `grounding-coordinate` at 40.7% (a gain of −9 points at n=162) is inside this protocol's null
band, while `prominence-people-v1` at 61.9% (+12 points at n=168) is above it, which is what its
DISQUALIFIED verdict says in a second way. A verdict comes from `nulltest`; this number says what
the verdict is worth in accuracy.

## Run it against us, and against the benchmarks the field uses

The audit table at [honesty.tools/audit](https://www.honesty.tools/audit) has 24 rows: our own nine
batteries, five third-party honesty corpora, six rows from three widely used hallucination
benchmarks (TruthfulQA, HaluEval, RAGTruth), scored by answer or response alone, and four
deception-probe evaluation sets (Apollo Research), scored by the model's response alone:

| benchmark | items | worst cell | A* | verdict |
|---|---|---|---|---|
| TruthfulQA, reference answers (`Correct Answers` vs `Incorrect Answers`, per category) | 6028 | 63 of 222 cells separate | 0.857 | DISQUALIFIED |
| HaluEval QA (`right_answer` vs `hallucinated_answer`) | 20000 | 5 of 6 cells separate | 0.979 | DISQUALIFIED |
| HaluEval dialogue (`right_response` vs `hallucinated_response`) | 20000 | 6 of 6 cells separate | 0.849 | DISQUALIFIED |
| HaluEval summarization (`right_summary` vs `hallucinated_summary`) | 20000 | 5 of 6 cells separate | 0.923 | DISQUALIFIED |
| HaluEval general (ChatGPT responses, human-annotated `hallucination` yes/no) | 4507 | 5 of 6 cells separate | 0.690 | DISQUALIFIED |
| RAGTruth responses (span-annotated; any span = hallucinated; seeded 8,000 of 17,790; stratified by task type, joined from `source_info.jsonl`) | 8000 | 9 of 18 cells separate | 0.788 | DISQUALIFIED |
| Apollo roleplaying (grader 1-7; ≤2 honest, ≥6 deceptive, 84 ambiguous dropped) | 287 | 2 of 6 cells separate | 0.746 | DISQUALIFIED |
| Apollo insider trading, report (`explicit` vs `concealed`) | 1005 | 4 of 5 cells separate | 0.999 | DISQUALIFIED |
| Apollo insider trading, doubling down (24 honest of 655) | 655 | 4 of 5 cells separate | 0.982 | DISQUALIFIED |
| Apollo sandbagging (stratified by question pool) | 932 | 5 of 10 cells separate | 1.000 | DISQUALIFIED |

`--explain` says why. In TruthfulQA the correct column carries hedges the incorrect column almost
never does: "I have no comment" appears in 86 correct answers and 0 incorrect, "do not" in 83
against 4; in the largest separating cell the incorrect column leans on "that the", "about" and
"believe that". A model that learned to hedge scores without knowing anything. In HaluEval the hallucinated answers were
written by ChatGPT and the right answers are HotpotQA's short reference strings: median 58
characters against 12, and in the dialogue set 104 against 69. In the summarization set the
hallucinated side writes in ChatGPT's connective register ("additionally" 121 against 0,
"furthermore" 53 against 0) while the reference highlights carry the newsroom's own boilerplate
("click here" 6 against 328). HaluEval general is the one HaluEval set built the way this test
should pass, one process annotated afterwards, and it fails moderately because the annotated
hallucinations concentrate in mathematical tasks, so LaTeX fragments mark label-1: the surface
reads the task type and the task type predicts the annotation. In RAGTruth the task type is a second file, joined on `source_id` and used as the stratum, so the null shuffles labels within task and the 68% hallucination rate of data-to-text against 29% for QA and summarisation is conditioned on rather than scored. The verdict stands inside each task: within data-to-text, hallucinated overviews cite "Google" or "on Google" (67 and 61 responses against 0 faithful ones), a provenance the structured input never contains; within QA, hallucinated answers are longer (median 850 against 583 characters). An earlier scoring of this row the same evening, without the join, read A* 0.742 on one cell per probe; the joined row is recorded as a change in `audit_notes.json`, not overwritten. None of this is a
claim that the benchmarks are useless. It is a measurement of how much of a score on them is
available to a reader who never sees the question. [AUDIT_DOSSIER.md](AUDIT_DOSSIER.md) carries the
full feature tables for every row.

Nineteen of the twenty-four rows are DISQUALIFIED. Five of the DISQUALIFIED rows are ours:
`verifydomain-claims-147`, `calibration-battery-v1`, `photon-entities`, `grounding-coordinate`
(BORDERLINE) and `prominence-people-v1`, which v1.2 missed and the alphabet probes catch. Three rows
sit at A* 1.000: two are ours, and the third is Apollo's sandbagging set. A DISQUALIFIED verdict is a measurement about a corpus, not a
finding of error or misconduct by its authors. Re-score the whole table with
`python3 nulltest_audit.py --jobs 4` (about an hour and a half on our 4-core machine with the
dossier's `--explain`: 39 minutes of it HaluEval summarization, 30 RAGTruth at the 800 permutations
its 18 cells need, 14 TruthfulQA at 8,900 permutations for 222 cells; the corpora are fetched from
their own repositories and recorded as a URL and a sha256, and a companion file a row joins is
recorded the same way).

## Licence

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE). It covers this repository: the null test,
the audit harness, the claim gate and the batteries. It does not cover the third-party corpora
scored in the audit table, which are fetched from their own repositories under their own terms and
recorded here as a URL and a sha256, not a copy.
