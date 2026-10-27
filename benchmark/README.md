# benchmark/: Honesty.tools flagship benchmark (work in progress)

The design has five parts: certified suite, paired probes, four-arm controls, persona probe leaderboard, and replay. This directory holds Phase 1, the certified suite.

- `certified/inventory.json`: every dataset with an /audit row, its nulltest v1.4 verdict and its license.
- `certified/certify.py`: a pair-level filter (AFLite-style) that writes a certificate in the `honesty.tools/certificate/v0` format. Certification has four gates, and all must pass:
  - in-family paired nulltest;
  - in-family unpaired nulltest (the probes the filter used, so these gates are circular);
  - **held-out probes** (`heldout.py`);
  - **negation-cue sign test**.
- `certified/heldout.py`: eight probe types the filter never sees:
  - negation, hedge and function-word counts;
  - punctuation, digits, capitals and mean word length;
  - a character 3- to 5-gram Naive Bayes, refit on every permutation.

  They use a paired sign-flip null with Westfall-Young correction. The file also has the exact negation sign test.
- `certified/calibrate_paired.py` and `certified/calibrate_heldout.py`: false-positive rates on real pairs whose labels are coin-flipped inside each pair.
- `certified/truthfulqa-binary/`: prototype on the Judging-by-the-Cover 790-pair set (arXiv 2609.13003, Apache-2.0). Contents:
  - `certificate.json` (status NOT_CERTIFIED);
  - `certified.json`, `removed.json`;
  - paired, unpaired and held-out reports;
  - paired-mode runs on the 790 and 476 sets.

Paired mode (honestytools 0.6.0):

```
honest paired PAIRS.json --explode right=0,wrong=1 --pair-key _row --stratum-key cat
```

Rerun the prototype:

```
python3 benchmark/certified/certify.py benchmark/certified/truthfulqa-binary/source_790.json --out DIR --name truthfulqa-binary-790
```
