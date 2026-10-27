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

## Results on TruthfulQA (the Judging-by-the-Cover 790-pair set, arXiv 2609.13003, Apache-2.0)

| split | pairs kept | status | what still fires |
|---|---|---|---|
| `truthfulqa-binary/`: filter with the floor | 405 of 790 (51.3%) | **NOT_CERTIFIABLE** (filter_reached_floor_with_cues) | paired lexicality A\*=0.773; held-out char n-gram 0.760, negation 0.649, caps 0.568 and hedge 0.556 (each adj p ≤ 0.0005); negation sign test 150 vs 29, p=7e-21 |
| `truthfulqa-binary-negbal/`: Phase 1b option A, existing pairs selected to balance negation within each category, no rewriting | 491 of 790 (62.2%) | **NOT_CERTIFIED** | paired lexicality A\*=0.773; unpaired length 0.587; held-out char n-gram 0.772 and hedge 0.580 (adj p 0.0005) |
| `certificate.v0-unconstrained.json`: the earlier filter with no floor | 49 of 790 | NOT_CERTIFIED | the cues still fired at 49 pairs |

**Reading option A honestly:**
- Its negation gate passes by construction (37 vs 37), so that pass is circular and certifies nothing.
- Balancing negation did not remove the other cues. A character n-gram model still separates right from wrong in 77% of pairs, and the right answer still hedges more.
- Rewriting the pairs with a model (option B) is not started.

## Files

- `certified/inventory.json`: every dataset with an /audit row, its nulltest v1.4 verdict and its license.
- `certified/certify.py`: a pair-level filter (AFLite-style) that stops at the floor and writes the certificate. `--no-filter --source SRC` certifies a given subset against its source.
- `certified/heldout.py`: the eight held-out probes and the exact negation sign test.
- `certified/balance_negation.py`: the option A selector (seeded; ties kept, and the larger side subsampled within each category).
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
