# FLOOR v1.1 certificates (run 2026-10-27 MDT)

FLOOR v1.1 changes one gate in UNPAIRED mode. The held-out probes are now scored twice on the same stratified permutation draws:

- **pooled**: identical to v1 (`heldout_np.evaluate_unpaired`; `tests/test_floor_v11.py` checks the output is equal);
- **within stratum**: for each probe, the size-weighted mean over strata of max(AUROC_s, 1 − AUROC_s), over every stratum holding both classes. A* is taken per stratum before averaging, so a cue that points in opposite directions in two strata does not cancel. Labels are permuted within stratum, the char n-gram is refit in every permutation, and Westfall-Young min-p runs over the eight probes.

The held-out gate fails if **either** family separates (adjusted p < 0.05) or is borderline (< 0.10). With one stratum the two families are identical. The filter, floor and statuses are unchanged, so for the same input the kept split is the same as v1's (checked by sha256 for every set below).

Why: when label base rates differ across strata and a probe can identify the stratum, the pooled AUROC mostly reads the stratum, and so does every stratified null. The pooled test then has little power against cues inside a stratum. The FLOOR paper's RAGTruth section works through a real case. `tests/test_floor_v11.py` plants a cue the pooled test misses (pooled adj p 0.34) and the within test catches (adj p 0.003).

Code: `../heldout_strat.py` and `../certify_unpaired_v11.py`. The v1 tools and the v1 certificates in `../floor-v1/` are unchanged and still replay. Certificate format: `floor/certificate/v1.1`.

Rebuild the inputs with `../floor-v1/make_inputs.py`, then, for example:

```
python3 benchmark/certified/certify_unpaired_v11.py INPUTS/aperture__postcutoff-trials-v1.json --out DIR --text-key entity
```

Pass `--stratum-key category` for the same sets as v1 (grounding-coordinate, photon-entities, knowledge-prominence-v1/v2/v3, calibration-battery-v1, bielik, ragtruth). knowledge-prominence-v3's input is `../knowledge-prominence-v3/items.json`. Kept and removed splits are committed only for `aperture__postcutoff-trials-v1/`, as in v1.

## Results: v1 → v1.1

STATUS_TABLE
