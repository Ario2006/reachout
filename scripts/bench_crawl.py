"""Benchmark the crawl engine on a fixed list of domains, cold then warm (page cache).

    PYTHONPATH=. .venv/bin/python scripts/bench_crawl.py                      # 50 domains, no LLM, 2 runs
    PYTHONPATH=. .venv/bin/python scripts/bench_crawl.py --limit 200 --domains scripts/bench_domains_200.txt
    PYTHONPATH=. .venv/bin/python scripts/bench_crawl.py --llm                # also run the local model (slow)

It runs the real pipeline (async fetcher, planner, extractor, MX, scoring, SQLite) in a throwaway data folder, so
it never touches ~/Reachout. Search is replaced by the fixed list, so numbers are comparable between runs.
Real network, so expect some variation; the shape (cache hit rate, requests, RSS) is stable."""
from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import tempfile
import time
from pathlib import Path


def rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(r / (1024 * 1024) if sys.platform == "darwin" else r / 1024, 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domains", default=str(Path(__file__).with_name("bench_domains.txt")))
    ap.add_argument("--limit", type=int, default=0, help="use only the first N domains")
    ap.add_argument("--concurrency", type=int, default=30)
    ap.add_argument("--runs", type=int, default=2, help="1 = cold only; 2 = cold then warm (page cache)")
    ap.add_argument("--llm", action="store_true", help="use the local model (Ollama must be running)")
    ap.add_argument("--http2", action="store_true", help="enable HTTP/2 (needs the h2 package)")
    ap.add_argument("--json", default="", help="write the results to this file")
    a = ap.parse_args()

    home = tempfile.mkdtemp(prefix="reachout-bench-")
    os.environ["REACHOUT_HOME"] = home
    from backend.core import afetch, config, db, discovery, jobs, pipeline
    from backend.core.job_handlers import HANDLERS
    from backend.core.models import AppSettings
    from backend.core.planner import Plan

    domains = [d.strip() for d in Path(a.domains).read_text().split() if d.strip()]
    domains = domains[: a.limit] if a.limit else domains
    config.ensure_dirs()
    engine = db.get_engine()
    db.migrate(engine)
    db.save_settings(AppSettings(target_leads=0, global_concurrency=a.concurrency, max_domains_per_run=max(len(domains), 1),
                                 max_pages_per_domain=6), engine)

    hits = [discovery.SearchHit(url=f"https://{d}/", query="bench") for d in domains]
    discovery.make_searcher = lambda s, p: (lambda q: hits)  # type: ignore[assignment]
    if not a.llm:
        pipeline.make_batcher = lambda s: None  # type: ignore[assignment]
    if a.http2:
        real = pipeline.make_fetcher

        def h2_fetcher(s, store):  # type: ignore[no-untyped-def]
            return afetch.AsyncFetcher(store, delay_s=(s.crawl_delay_min_s, s.crawl_delay_max_s), total_timeout_s=s.fetch_timeout_s,
                                       global_limit=s.global_concurrency, ttl_s=s.page_cache_ttl_days * 86400, user_agent=afetch.build_user_agent(s.crawler_contact), http2=True)
        pipeline.make_fetcher = h2_fetcher  # type: ignore[assignment]

    results = []
    for i in range(a.runs):
        label = "cold" if i == 0 else "warm"
        plan = Plan(mode="service", search_queries=["bench"])
        run = pipeline.create_run(f"bench {label}", plan, pipeline.RunOptions(recrawl=True, expand_lists=False, max_domains=len(domains)), engine)
        t0 = time.monotonic()
        jobs.run_job(jobs.claim_next(engine), HANDLERS, engine)
        elapsed = time.monotonic() - t0
        r = pipeline.get_run(run["id"], engine)
        s, e = r["stats"], r["stats"].get("engine", {})
        llm_calls = (e.get("llm") or {}).get("calls", 0)
        results.append({
            "run": label, "state": r["state"], "seconds": round(elapsed, 1), "domains": s.get("domains_done", 0),
            "domains_per_min": round(s.get("domains_done", 0) / elapsed * 60, 1), "pages": s.get("pages", 0),
            "pages_per_sec": round(s.get("pages", 0) / elapsed, 2), "requests": e.get("requests", 0),
            "cache_hit_rate": e.get("cache_hit_rate", 0), "tiers": e.get("tiers", {}), "retries": e.get("retries", 0),
            "failures": e.get("failures", {}), "leads": s.get("leads_new", 0) + s.get("leads_dup", 0),
            "llm_calls": llm_calls, "llm_calls_per_100_leads": round(llm_calls / max(s.get("leads_new", 0) + s.get("leads_dup", 0), 1) * 100, 1),
            "stage_seconds": e.get("stage_seconds", {}), "peak_rss_mb": rss_mb(),
        })
        r0 = results[-1]
        print(f"[{label}] {r0['state']}: {r0['domains']} domains in {r0['seconds']} s = {r0['domains_per_min']}/min, "
              f"{r0['pages']} pages ({r0['pages_per_sec']}/s), cache hit {r0['cache_hit_rate']:.0%}, requests {r0['requests']}, "
              f"leads {r0['leads']}, LLM calls {llm_calls}, peak RSS {r0['peak_rss_mb']} MB", flush=True)
    out = {"domains_file": a.domains, "n_domains": len(domains), "concurrency": a.concurrency, "llm": a.llm, "http2": a.http2, "runs": results}
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=2))
    print(json.dumps(out["runs"][-1] if a.runs == 1 else {"cold": results[0], "warm": results[1]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
