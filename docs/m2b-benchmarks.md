# Milestone 2b: crawl engine benchmarks

Measured on the 8 GB M3 Mac, real network, throwaway data folder, `scripts/bench_crawl.py` (fixed domain lists
`scripts/bench_domains.txt` = 50 domains, `scripts/bench_domains_200.txt` = 200). Search is replaced by the list, so
runs are comparable. The "before" numbers come from the old threaded synchronous crawler run on the same 50 domains
before it was removed (30 threads, no model). Network variance is real: three identical 50-domain cold runs of the
new engine took 30.6 s, 29.9 s and 40.4 s.

## 50 domains, no model

| | Before (30 threads) | After, cold | After, 2nd run (cache) |
| --- | --- | --- | --- |
| Wall time | 56 s | 30 to 40 s | 13.8 s |
| Domains per minute | 53 | 74 to 100 | 218 |
| Pages per second | 2.9 | 3.9 to 5.3 | 11.7 |
| Network requests | not counted (33% were blind 404 guesses) | 196 to 212 (about 10% 404) | **0** |
| Cache hit rate | none | 0% | **100%** |
| Peak RSS | 126 MB | 160 to 204 MB | 231 MB |

With the old crawler's 4 threads (the M2 default) the same list took 147 s (20 domains/min).

## 200 domains, no model

| | Cold | 2nd run |
| --- | --- | --- |
| Wall time | **140 s** (target: under 10 min) | 21 s |
| Domains per minute | 86 | 574 |
| Pages (per second) | 702 (5.0) | 701 (33.6) |
| Requests | 887 | 8 |
| Cache hit rate | 0% | 100% |
| **Peak RSS** | **299 MB** (target: under about 1.5 GB) | 308 MB |
| Leads | 158 | 158 (all already known) |

## The language model (50 domains, real `qwen2.5:3b-instruct`)

| | Before | After |
| --- | --- | --- |
| LLM calls per 100 leads | about 300 to 340 | **39.5** (15 calls for 38 leads) |
| Wall time, 50 domains | not measured as a set; M2 searches took 5 to 9 min | 198 s |

The calls fell because page structure (JSON-LD, microdata, repeated team cards, heading and title pairs) answers
most team pages, only pages it cannot read go to the model, and those share batched calls (about 3,000 tokens each).
The model is the slowest stage, and it runs while the crawl continues.

## Other findings

* **HTTP/2** (`h2`) was 10 to 20% faster in two paired runs, within the run-to-run noise, and a site is fetched one
  request at a time, so multiplexing has little to work with. Not adopted; `bench_crawl.py --http2` exists if you
  install `h2` and want to retest.
* **Browser tier**: implemented and lazy. Playwright and Chromium are **not installed here, so it has only been
  tested against a stub, not a real JavaScript page**. About 3.6% of pages in the sample were JS shells.
* Second-run time is now dominated by DNS (MX lookups) and extraction rather than the network.

## Not measured

* A 200-domain run of the old crawler (extrapolating its 53 domains/min gives about 3.8 minutes; not run).
* Lead quality against a labelled set: yields and relevance still depend on what the search engine returns
  (see `docs/m2-yields.md`).
