# FLOOR v1 certificates (run 2026-10-27 MDT)

There is one directory per public dataset. Each holds `certificate.json` (format `floor/certificate/v1`), plus the in-family and held-out test outputs. The status table and how to read it are in `../../README.md`.

Kept and removed splits are committed only for `aperture__postcutoff-trials-v1/`, the one CERTIFIED set; it is ours, Apache-2.0. For the others, a NOT_CERTIFIABLE kept set isn't a benchmark, and third-party data isn't copied here (see NOTICE). Every certificate records the sha256 of its source and its split.

Rebuild the inputs (byte-identical to the ones certified), then rerun one set:

```
python3 benchmark/certified/floor-v1/make_inputs.py INPUTS
python3 benchmark/certified/certify_unpaired.py INPUTS/aperture__postcutoff-trials-v1.json --out DIR --text-key entity
python3 benchmark/certified/certify_paired_np.py INPUTS/halueval__qa.json --out DIR --name halueval__qa-sample2000
```

Pass `--stratum-key category` to `certify_unpaired.py` for sets with more than one category: grounding-coordinate, photon-entities, knowledge-prominence-v1/v2, calibration-battery-v1, bielik and ragtruth. The three scripts need numpy.
