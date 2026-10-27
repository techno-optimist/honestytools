# knowledge-prominence-v3 (built 2026-10-27 MDT): NOT_CERTIFIABLE under FLOOR v1

Two classes of **real** entities. label 0 is well known: mean daily en.wikipedia user pageviews of at least 50. label 1 is little known: at most 10 a day, from the **same** Wikipedia seed category. Built from scratch with a recorded builder. It replaces knowledge-prominence-v2, which was below the floor at 148 pairs and has no recorded builder.

## Result

**NOT_CERTIFIABLE**, reason `filter_reached_floor_with_cues`. See `certificate/certificate.json`.

| | |
|---|---|
| source | 1,968 items (984 pairs; 984 per class) |
| floor | 984 items (≥320 per class) |
| filter stopped at | 1,011 items (505 / 506) |
| in-family unpaired | DISQUALIFIED: worst cell lexicality/person A\* 0.751, adj p 0.0092 |
| held-out probes | fail: char n-gram A\* 0.761 and mean word length A\* 0.590 (each adj p 0.0005); caps 0.516, function words 0.503 and hedge 0.502 pass; negation, punctuation and digits are absent |
| negation cue | absent (no item has one) |

**What this says.**
- Exact matching on surface form removed the cues it targeted: punctuation, digits, capitals, function words and length.
- It did not remove name *content*. Well-known names are built from more common name parts, and a character n-gram model reads that.
- The filter could not remove this cue before reaching the floor.
- The failure was found after the design was fixed. The design was not tuned afterwards to make it pass.

## Files

- `build_kp_v3.py`: the builder.
  - `fetch` lists the seed categories' direct article members and samples them (seeded). It then reads daily pageviews and writes `candidates.json.gz`.
  - `build` is offline and deterministic. It turns `candidates.json.gz` into `battery.json` (with provenance) and `items.json` (the certifier input).
- `candidates.json.gz`: every sampled title with its daily views (20,796 rows). It makes `build` replayable byte for byte (except the `built` date).
- `battery.json`: the items plus `provenance`, which records:
  - the seed categories;
  - the pageview window (2026-08-28 to 2026-10-25, 59 days);
  - the thresholds (≥50 and ≤10 mean daily user views; items in between are dropped);
  - the form filter;
  - the pairing rules;
  - the seed (20261027);
  - per-seed counts and exclusions;
  - sha256 hashes of the builder and the candidates.
- `items.json`: the certifier input, `[{entity, label, category, views, seed_category, title, pair}]`.
- `certificate/`: `certificate.json`, `heldout.out.json` and `unpaired.out.json`. The kept and removed splits aren't committed, because a NOT_CERTIFIABLE kept set isn't a benchmark (as in `floor-v1/`).

## Design

**Seed categories.** Direct article members only (namespace 0), with no subcategory traversal; traversal is what put fictional characters into v1. The categories are listed in the builder.

**Pairs per domain:**

| domain | pairs |
|---|---|
| person | 881 |
| place | 33 |
| book | 30 |
| film | 27 |
| firm | 13 |

Persons dominate because those categories are the largest.

**Form filter.**
- Only ASCII letters and spaces, 1 to 5 words, 4 to 30 characters.
- No "List of".
- A ", Texas"/", Ohio"/", Massachusetts" suffix is removed from those place titles first.
- A name found under two seed categories is dropped.

**Pairing.** Within a seed category, each pair matches exactly on:
- word count;
- the capitalisation shape of each word;
- the function words and their positions;
- the first letter;
- the last two letters.

Total length is within one character. The pools are shuffled with the seed, and each high item takes the first unused match.

**Pageviews.** The MediaWiki `prop=pageviews` API (daily user views, fetched 2026-10-27 MDT). A null day counts as 0.

**Labels.** The labels are pageview bands, an external popularity signal. They are not a measurement of what any model knows.

**Data.** Titles are Wikipedia article titles. The counts are Wikimedia pageview statistics, which are published under CC0. This compilation is Apache-2.0.

## Replay

```
python3 benchmark/certified/knowledge-prominence-v3/build_kp_v3.py build --candidates benchmark/certified/knowledge-prominence-v3/candidates.json.gz --out /tmp/kp3/battery.json
sha256sum /tmp/kp3/items.json   # f4d2a8eb0e82618f377925f126e1e4c8de76be31d75f7d05f45d401354e1b6b1
python3 benchmark/certified/certify_unpaired.py /tmp/kp3/items.json --out /tmp/kp3/cert --name aperture__knowledge-prominence-v3 --text-key entity --stratum-key category --n-perm 600
```
