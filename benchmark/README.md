# benchmark/ — Honesty.tools flagship benchmark (work in progress)

Design: five parts (certified suite, paired probes, four-arm controls, persona probe leaderboard,
replay). This directory holds Phase 1, the certified suite.

- `certified/inventory.json` — every dataset with a /audit row, its nulltest v1.4 verdict and license.
- `certified/certify.py` — pair-level filter + certificate (`honesty.tools/certificate/v0`).
- `certified/calibrate_paired.py` — false-positive rate of paired mode on real, label-scrambled pairs.
- `certified/truthfulqa-binary/` — prototype on the Judging-by-the-Cover 790-pair set
  (arXiv 2609.13003, Apache-2.0): `certificate.json` (status NOT_CERTIFIED), `certified.json`,
  `removed.json`, paired/unpaired reports, and paired-mode runs on the 790 and 476 sets.

Paired mode: `python3 nulltest.py PAIRS.json --explode right=0,wrong=1 --pair-key _row --stratum-key cat`.
