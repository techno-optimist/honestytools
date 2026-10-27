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

| dataset | strata scored | v1 → v1.1 status | v1.1 failing gates | pooled worst (= v1) | within-stratum worst | changed |
|---|---|---|---|---|---|---|
| aperture/postcutoff-trials-v1 | 1 | CERTIFIED → **CERTIFIED** | none | function: A* 0.550, adjusted p 0.1334 | function: A* 0.550, adjusted p 0.1334 | no |
| aperture/calibration-battery-v1 | 6 | NOT_CERTIFIABLE → **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes | char_ngram: A* 0.986, adjusted p 0.0004998 | char_ngram: A* 0.993, adjusted p 0.0004998 | no |
| aperture/grounding-coordinate | 6 | NOT_CERTIFIABLE → **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes | word_len: A* 0.628, adjusted p 0.0004998 | char_ngram: A* 0.726, adjusted p 0.004998 | no |
| aperture/knowledge-prominence-v1 | 5 | NOT_CERTIFIABLE → **NOT_CERTIFIABLE** | held_out_probes | caps: A* 0.550, adjusted p 0.07846 | caps: A* 0.555, adjusted p 0.5542 | no |
| aperture/knowledge-prominence-v2 | 5 | NOT_CERTIFIABLE → **NOT_CERTIFIABLE** | none | caps: A* 0.549, adjusted p 0.1729 | function: A* 0.538, adjusted p 0.5982 | no |
| aperture/knowledge-prominence-v3 | 5 | NOT_CERTIFIABLE → **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes | char_ngram: A* 0.761, adjusted p 0.0004998 | char_ngram: A* 0.766, adjusted p 0.0004998 | no |
| aperture/photon-entities | 4 | NOT_CERTIFIABLE → **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes | char_ngram: A* 0.865, adjusted p 0.0004998 | word_len: A* 0.803, adjusted p 0.0004998 | no |
| aperture/prominence-people-v1 | 1 | NOT_CERTIFIABLE → **NOT_CERTIFIABLE** | in_family_unpaired | caps: A* 0.554, adjusted p 0.1989 | caps: A* 0.554, adjusted p 0.1989 | no |
| aperture/prominence-people-v2 | 1 | NOT_CERTIFIABLE → **NOT_CERTIFIABLE** | held_out_probes | word_len: A* 0.648, adjusted p 0.003998 | word_len: A* 0.648, adjusted p 0.003998 | no |
| aperture/verifydomain-claims-147 | 1 | NOT_CERTIFIABLE → **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes | char_ngram: A* 0.941, adjusted p 0.0004998 | char_ngram: A* 0.941, adjusted p 0.0004998 | no |
| bielik-entity-familiarity-v2 | 4 | NOT_CERTIFIABLE → **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes | char_ngram: A* 0.846, adjusted p 0.0004998 | char_ngram: A* 0.854, adjusted p 0.0004998 | no |
| hallucinated-references/gpt-4 | 1 | NOT_CERTIFIABLE → **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes | char_ngram: A* 0.765, adjusted p 0.0004998 | char_ngram: A* 0.765, adjusted p 0.0004998 | no |
| halueval/general | 1 | NOT_CERTIFIABLE → **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes, negation_cue_absent | char_ngram: A* 0.617, adjusted p 0.0004998 | char_ngram: A* 0.617, adjusted p 0.0004998 | no |
| ragtruth/responses | 3 | NOT_CERTIFIABLE → **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes | caps: A* 0.608, adjusted p 0.0004998 | punct: A* 0.613, adjusted p 0.0004998 | no |
| truth-is-universal/facts | 1 | NOT_CERTIFIABLE → **NOT_CERTIFIABLE** | in_family_unpaired, held_out_probes, negation_cue_absent | negation: A* 0.544, adjusted p 0.0004998 | negation: A* 0.544, adjusted p 0.0004998 | no |

**No verdict changed.** On all 15 sets the v1.1 status, the three gates and the status reasons equal v1's. `aperture/postcutoff-trials-v1` is still the only CERTIFIED set (one stratum, so its within-stratum family equals the pooled one). knowledge-prominence-v2 still passes every gate and is still below the floor.

What within-stratum scoring adds (probes that separate within stratum but not pooled):

- aperture/grounding-coordinate: char_ngram (within A* 0.726, adjusted p 0.004998);
- aperture/photon-entities: digits, caps;
- ragtruth/responses: function, digits;
- aperture/calibration-battery-v1 goes the other way: function separates pooled but not within stratum.

RAGTruth, the case that motivated v1.1: on the kept split (2,052 items, same sha256 as v1) the within-stratum char n-gram is A* 0.541, adjusted p 0.331, so it does **not** separate after Westfall-Young (pooled: A* 0.655, adjusted p 0.875). The paper's within-task n-gram separation was measured on the 4,000-item source split; it does not carry over to the kept split at this correction level. RAGTruth fails the held-out gate either way, on hedge, punct and caps (both families) plus function and digits (within only); the worst within-stratum probe is punct, A* 0.613, adjusted p 0.0004998.
