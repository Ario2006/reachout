# Milestone 2: measured lead yields

Real runs of the final pipeline, on this machine (M3 MacBook, 8 GB), with the real search engine (DuckDuckGo
via `ddgs`), real crawling, and `qwen2.5:3b-instruct` through Ollama. Each run was a fresh data folder started
with `python -m backend.cli discover "<request>" --max-domains 50`. Every lead is MX-valid and has a source URL.

| Request | Leads | Time | Sites crawled (failed) | Sites with leads | Personal | Generic | Guessed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| AI/ML internship openings at startups in Bangalore | **32** | 7.6 min | 49 (8) | 15 | 1 | 28 | 3 |
| Pre-seed investors who back AI developer tools in India/SEA | **36** | 8.6 min | 50 (5) | 25 | 1 | 33 | 2 |
| Founders of D2C brands who need a website redesign | **38** | 5.4 min | 50 (2) | 25 | 0 | 30 | 8 |
| **Total** | **106** | | | | **2 (1.9%)** | **91** | **13** |

All three reach the 30-lead target in under 15 minutes. The investor and D2C runs needed a second round
(the pipeline widens automatically when a run is short of the target).

## What the numbers do not say

- **`published_personal` is rare, and that is the honest answer.** Small companies publish `info@`,
  `hello@` and `careers@`, not named addresses. A lead is only `personal` when a name found on the page
  matches the address, so 2 of 106 qualified. An earlier version labelled any name-looking address personal
  (56% of leads in one run); that was wrong: `payments@` and `edi@` were counted as people.
- **Relevance is the real limit, not volume.** Search engines return job boards, directories, news sites and
  large corporations. Roughly 40% of the sites in the Bangalore run were genuine target startups; the investor
  run had about a third actual investment firms, the rest AI developer-tool companies; the D2C run, after a
  planner fix, was mostly real D2C brands (Brooklinen, Glossier, Daily Harvest, Mejuri, Bark, Qikink).
- **Results vary a lot between runs of the same request.** DuckDuckGo rate-limits, so some queries return
  nothing. The same Bangalore request produced 19, 32 and 48 leads across different runs and code versions.
  Widening makes the 30-lead target reliable; it does not make the lead mix identical.
- **Guessed addresses are low value.** 13 of 106. They are only MX-checked, never verified, so they are
  excluded from sending by default.

## What was wrong, and fixed, on the way

| Observed in a real run | Fix |
| --- | --- |
| Queries about "internship jobs" returned job boards | Job-listing queries are dropped; company-page and "top N" list queries are always added |
| Service-mode queries ("website redesign D2C brands") returned design agencies, not brands | Service-mode queries about the service itself are dropped; regions must appear in the request |
| The model returned job titles and company names as "people" | Names must appear on the page and pass a title/department word filter |
| Famous people named on directory pages produced guessed addresses (e.g. `ilya@…`) | Guesses only for target-role people on the site's own team page, with a plain-title role |
| 40 of 72 leads were IBM regional offices | At most 5 leads per site (setting), best addresses first for the kind of outreach |
| Universities, regulators, WhatsApp and calendar links were crawled | Institutional domains, link shorteners and tool sites are blocked |
| `customersupport@…` labelled as a person named "Customer Support" | Desk, newsroom, IR and technical mailboxes are excluded; a name beside an address must use both first and last name |
| Four LLM calls on job-listing pages | The model is never called on job, portfolio, news or list pages |

## Resource use

- Ollama holds the 3B model at about 1.9 GB resident while a run is active. The crawling process itself was
  small (25 MB when measured at the start of a run; its peak was not captured).
- Only one model call runs at a time. The model is called about 100 times per run (about 2 per site).
