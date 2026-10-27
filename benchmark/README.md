# benchmark/: FLOOR, the Honesty.tools flagship benchmark (work in progress)

FLOOR has five parts:
- certified splits (the pairs left after surface cues are removed);
- paired probes;
- four-arm controls;
- a persona probe leaderboard;
- replay.

This directory holds Phase 1, the certified splits.

## Certificate statuses (format `floor/certificate/v1`)

| status | meaning |
|---|---|
| `CERTIFIED` | all four gates pass on a kept set at or above the floor |
| `NOT_CERTIFIED` | the kept set is above the floor, but at least one gate fails; `status_reasons` names which |
| `NOT_CERTIFIABLE` | the dataset can't be cleaned without collapsing. Either the filter reached the floor with cues still firing, or the source or kept set is below the floor |

**Floor:** max(ceil(0.50 × source pairs), 320).

- **Why 0.50.** A certificate has to describe the benchmark it names. Suppose more than half the pairs must go before the cues stop firing. Then the cue is a property of how the benchmark was built, and the kept minority is a different, smaller benchmark.
- **Why 320.** It is the smallest n at which an exact two-sided sign test has at least 80% power to detect a held-out probe that picks the right answer 60% of the time. The test runs at alpha 0.05/8, Bonferroni over the eight held-out probes, which is conservative next to the Westfall-Young correction actually used. Below 320 pairs, "no cue found" mostly means "too few pairs to find one".
- **Tested in** `tests/test_floor.py`: the 320 itself, plus 199 at k=1 and 142 against a 0.65 probe.

## Gates

All four must pass:
- in-family paired nulltest;
- in-family unpaired nulltest. These are the probes the filter used, so these two gates are circular;
- **held-out probes** (`heldout.py`): eight probe types the filter never sees, under a paired sign-flip null with Westfall-Young correction;
- **negation-cue sign test** (exact).

## Results on TruthfulQA (the Judging-by-the-Cover 790-pair set, arXiv 2609.13003, Apache-2.0): final NOT_CERTIFIABLE

| split | pairs kept | status | what still fires |
|---|---|---|---|
| `truthfulqa-binary/`: filter with the floor | 405 of 790 (51.3%) | **NOT_CERTIFIABLE** (filter_reached_floor_with_cues) | paired lexicality A\*=0.773; held-out char n-gram 0.760, negation 0.649, caps 0.568 and hedge 0.556 (each adj p ≤ 0.0005); negation sign test 150 vs 29, p=7e-21 |
| `truthfulqa-binary-negbal/`: Phase 1b option A, existing pairs selected to balance negation within each category, no rewriting | 491 of 790 (62.2%) | **NOT_CERTIFIED** | paired lexicality A\*=0.773; unpaired length 0.587; held-out char n-gram 0.772 and hedge 0.580 (adj p 0.0005) |
| `certificate.v0-unconstrained.json`: the earlier filter with no floor | 49 of 790 | NOT_CERTIFIED | the cues still fired at 49 pairs |

**Reading option A honestly:**
- Its negation gate passes by construction (37 vs 37), so that pass is circular and certifies nothing.
- Balancing negation did not remove the other cues. A character n-gram model still separates right from wrong in 77% of pairs, and the right answer still hedges more.

**Option B, model rewrites (pilot, 100 pairs):** `truthfulqa-binary/optb-pilot/`, a negative-result artifact and **not a certified split**. phi-4 rewrote both answers of each pair; an NLI check (deberta-large-mnli) kept 82. On those 82 the length gap narrowed and the hedge cue weakened, but the negation cue stayed (31 vs 3) and the character n-gram probe got stronger (0.780, against 0.707 on the same 82 originals).

**Decision: TruthfulQA is final NOT_CERTIFIABLE for FLOOR v1.** Options A and B are kept as documented negative findings. In TruthfulQA, negation is part of the correct answer's content ("No, ...", "Nothing happens"). Removing it by selection or by meaning-preserving rewriting either fails or changes the answers.

<!-- floor-v1-status:start -->
## FLOOR v1 status (run 2026-10-27 MDT)

FLOOR v1 was run on every dataset in `certified/inventory.json` with a clear permissive licence. Paired sets used `certify_paired_np.py`; unpaired sets used `certify_unpaired.py`. Every certificate is in `certified/floor-v1/<dataset>/certificate.json`. Inputs rebuild byte-for-byte with `certified/floor-v1/make_inputs.py`.

| dataset | licence | n source → kept | status | failing gates | reasons | worst held-out probe on kept |
|---|---|---|---|---|---|---|
| aperture/postcutoff-trials-v1 | Apache-2.0 (ours) | 702 → 702 items | **CERTIFIED** | none | - | function: A* 0.550, adjusted p 0.1334 over 8 probes |
| truthfulqa-binary (790-pair set; FINAL for v1) | Apache-2.0 | 790 → 405 pairs | **NOT_CERTIFIABLE** | in_family_paired, in_family_unpaired, held_out_probes, negation_cue_absent | filter_reached_floor_with_cues | char n-gram 0.760; negation sign test 150 vs 29 |
| aperture/calibration-battery-v1 | Apache-2.0 (ours) | 168 → 168 items | **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes | source_below_floor, kept_below_floor, filter_reached_floor_with_cues | char_ngram: A* 0.986, adjusted p 0.0004998 over 8 probes |
| aperture/grounding-coordinate | Apache-2.0 (ours) | 162 → 162 items | **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes | source_below_floor, kept_below_floor, filter_reached_floor_with_cues | word_len: A* 0.628, adjusted p 0.0004998 over 8 probes |
| aperture/knowledge-prominence-v1 | Apache-2.0 (ours) | 324 → 324 items | **NOT_CERTIFIABLE** | held_out_probes | source_below_floor, kept_below_floor | caps: A* 0.550, adjusted p 0.07846 over 8 probes |
| aperture/knowledge-prominence-v2 | Apache-2.0 (ours) | 296 → 296 items | **NOT_CERTIFIABLE** | none | source_below_floor, kept_below_floor | caps: A* 0.549, adjusted p 0.1729 over 8 probes |
| aperture/photon-entities | Apache-2.0 (ours) | 103 → 103 items | **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes | source_below_floor, kept_below_floor, filter_reached_floor_with_cues | char_ngram: A* 0.865, adjusted p 0.0004998 over 8 probes |
| aperture/prominence-people-v1 | Apache-2.0 (ours) | 168 → 168 items | **NOT_CERTIFIABLE** | in_family_unpaired | source_below_floor, kept_below_floor, filter_reached_floor_with_cues | caps: A* 0.554, adjusted p 0.1989 over 8 probes |
| aperture/prominence-people-v2 | Apache-2.0 (ours) | 156 → 156 items | **NOT_CERTIFIABLE** | held_out_probes | source_below_floor, kept_below_floor | word_len: A* 0.648, adjusted p 0.003998 over 8 probes |
| aperture/verifydomain-claims-147 | Apache-2.0 (ours) | 147 → 147 items | **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes | source_below_floor, kept_below_floor, filter_reached_floor_with_cues | char_ngram: A* 0.941, adjusted p 0.0004998 over 8 probes |
| bielik-entity-familiarity-v2 | MIT | 1,440 → 1,440 items | **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes | source_below_floor, kept_below_floor, filter_reached_floor_with_cues | char_ngram: A* 0.846, adjusted p 0.0004998 over 8 probes |
| hallucinated-references/gpt-4 | MIT | 1,000 → 697 items | **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes | filter_reached_floor_with_cues | char_ngram: A* 0.765, adjusted p 0.0004998 over 8 probes |
| halueval/dialogue | MIT | 2,000 → 1,027 pairs (seeded 2,000-pair sample of 10,000) | **NOT_CERTIFIABLE** | in_family_paired, in_family_unpaired, held_out_probes, negation_cue_absent | filter_reached_floor_with_cues | char_ngram: A* 0.823, adjusted p 0.0004998 over 8 probes |
| halueval/general | MIT | 4,507 → 2,314 items | **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes, negation_cue_absent | filter_reached_floor_with_cues | char_ngram: A* 0.617, adjusted p 0.0004998 over 8 probes |
| halueval/qa | MIT | 2,000 → 1,027 pairs (seeded 2,000-pair sample of 10,000) | **NOT_CERTIFIABLE** | in_family_paired, in_family_unpaired, held_out_probes, negation_cue_absent | filter_reached_floor_with_cues | punct: A* 0.924, adjusted p 0.0004998 over 8 probes |
| halueval/summarization | MIT | 2,000 → 1,027 pairs (seeded 2,000-pair sample of 10,000) | **NOT_CERTIFIABLE** | in_family_paired, in_family_unpaired, held_out_probes | filter_reached_floor_with_cues | char_ngram: A* 0.944, adjusted p 0.0004998 over 8 probes |
| ragtruth/responses | MIT | 4,000 → 2,052 items (seeded 4,000-item stratified sample of the audit's 8,000) | **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes | filter_reached_floor_with_cues | caps: A* 0.608, adjusted p 0.0004998 over 8 probes |
| truth-is-universal/facts | MIT | 561 → 561 items | **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes, negation_cue_absent | source_below_floor, kept_below_floor, filter_reached_floor_with_cues | negation: A* 0.544, adjusted p 0.0004998 over 8 probes |

**Reading the table.**
- **One dataset is CERTIFIED: `aperture/postcutoff-trials-v1`** (ours, 702 items, 351 per class). The filter removed nothing. The in-family screen gave adj p 0.74 on the whole set, and all eight held-out probes and the negation test pass. A certificate covers the probe families in this README, not a model-based reader.
- **Too small:** `aperture/knowledge-prominence-v2` passes every gate, but at 296 items it is below the floor (640 items, 320 per class). "No cue found" at that size is not evidence.
- **Borderline counts as failing:** a gate fails when its worst adjusted p is under 0.10 (twice alpha), as in `certify.py`. For example, `aperture/knowledge-prominence-v1` fails held-out on caps at adj p 0.078.
- **Everything else** still fires cues after the filter reached the floor, or was below the floor and already fails.
- **Unpaired floor:** items ≥ max(ceil(0.5 × n), 640), and each class ≥ 320. This reuses the paired power floor (320) per class as a conservative choice; no separate unpaired derivation was done.
- **Unpaired gates:**
  - in-family stratified nulltest (circular, as in paired mode);
  - held-out probes: AUROC, labels permuted within stratum, Westfall-Young over 8 probes;
  - negation: a two-sided permutation test on the difference in mean negation count.
- **Samples:** the HaluEval paired sets use a seeded 2,000-pair sample of each set's 10,000 pairs. RagTruth uses a seeded 4,000-item stratified sample of the audit's 8,000 (itself a seeded subsample of 17,790). This keeps run time bounded. A full-size run could differ.
- **Not run or not listed here:**
  - geometry-of-truth (2 sets) and Apollo (4 sets): no licence found, licence requests pending. Their status was computed privately and is not published.
  - anti-factual-personas: not in `inventory.json` and has no licence; not run.
  - truthfulqa/answers: covered by the 790-pair set above.
<!-- floor-v1-status:end -->

## Files

- `certified/inventory.json`: every dataset with an /audit row, its nulltest v1.4 verdict and its license.
- `certified/certify.py`: a pair-level filter (AFLite-style) that stops at the floor and writes the certificate. `--no-filter --source SRC` certifies a given subset against its source.
- `certified/heldout.py`: the eight held-out probes and the exact negation sign test.
- `certified/balance_negation.py`: the option A selector (seeded; ties kept, and the larger side subsampled within each category).
- `certified/certify_unpaired.py`: the FLOOR v1 filter and certificate for unpaired (label 0/1) sets.
- `certified/certify_paired_np.py`: `certify.py` with the numpy held-out backend, for large paired sets.
- `certified/heldout_np.py`: a vectorised version of `heldout.py`. In paired mode it reproduces `heldout.evaluate`'s permutation draws (checked in `tests/test_floor_v1.py`). It adds an unpaired mode. numpy is a benchmark-only dependency; the stable tools stay stdlib-only.
- `certified/floor-v1/`: the FLOOR v1 certificates, one directory per public dataset.
- `certified/calibrate_paired.py`, `certified/calibrate_heldout.py`: false-positive rates on real pairs with labels coin-flipped inside each pair.

## Rerun

Paired mode (honestytools 0.6.0+):

```
honest paired PAIRS.json --explode right=0,wrong=1 --pair-key _row --stratum-key cat
```

TruthfulQA with the floor, about 6 min:

```
python3 benchmark/certified/certify.py benchmark/certified/truthfulqa-binary/source_790.json --out DIR --name truthfulqa-binary-790
```

Option A:

```
python3 benchmark/certified/balance_negation.py benchmark/certified/truthfulqa-binary/source_790.json SEL.json
python3 benchmark/certified/certify.py SEL.json --no-filter --source benchmark/certified/truthfulqa-binary/source_790.json --out DIR --name truthfulqa-binary-790-negbal
```
