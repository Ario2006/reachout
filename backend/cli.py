"""Developer tool: run the lead pipeline from the terminal with live output.

    python -m backend.cli discover "AI internships in Bangalore" --max-domains 40

Uses the real search engine, real crawling and the configured Ollama model. Set REACHOUT_HOME
to keep the data away from your real ~/Reachout.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from collections import Counter

from backend.core import config, db, jobs, leads, pipeline, planner
from backend.core.job_handlers import HANDLERS
from backend.core.models import Mode


def _tail_log(run_id: int, stop: threading.Event, engine) -> None:
    last = 0
    while not stop.is_set():
        for row in pipeline.run_log(run_id, after=last, engine=engine):
            last = row["id"]
            print(f"  {row['created_at'][11:19]}  {row['message']}", flush=True)
        stop.wait(1.0)


def discover(args: argparse.Namespace) -> int:
    config.ensure_dirs()
    engine = db.get_engine()
    db.migrate(engine)
    settings = db.load_settings(engine)
    t0 = time.monotonic()

    result = planner.make_plan(args.request, settings, Mode(args.mode) if args.mode else None)
    plan = result.plan
    print(f"\nPlan ({result.source}{': ' + result.note if result.note else ''})")
    print(f"  mode={plan.mode.value} roles={plan.target_roles} industries={plan.industries} regions={plan.regions}")
    for q in plan.search_queries:
        print(f"  - {q}")
    plan_s = time.monotonic() - t0

    run = pipeline.create_run(args.request, plan, pipeline.RunOptions(max_domains=args.max_domains, use_overpass=args.overpass), engine)
    stop = threading.Event()
    threading.Thread(target=_tail_log, args=(run["id"], stop, engine), daemon=True).start()
    print(f"\nRunning (plan took {plan_s:.1f}s)…")
    t1 = time.monotonic()
    jobs.run_job(jobs.claim_next(engine), HANDLERS, engine)
    elapsed = time.monotonic() - t1
    stop.set()
    time.sleep(1.2)

    final = pipeline.get_run(run["id"], engine) or {}
    rows, total = leads.query_leads(leads.LeadFilter(run_id=run["id"]), "email_confidence:asc,company:asc", 0, 5000, engine)
    conf = Counter(r["email_confidence"] for r in rows)
    domains_ = Counter(r["domain"] for r in rows)
    st = final.get("stats", {})
    print("\n" + "=" * 70)
    print(f"state={final.get('state')}  elapsed={elapsed / 60:.1f} min  (search+crawl+extract+validate)")
    print(f"queries={st.get('queries_done')}/{st.get('queries_total')}  search_hits={st.get('search_hits')}  "
          f"sites={st.get('domains_done')}/{st.get('domains_total')} (failed {st.get('domains_failed', 0)})  pages={st.get('pages')}")
    print(f"emails_found={st.get('emails_found')}  people={st.get('people_found')}  llm_calls={st.get('llm_calls')}  dropped(no MX)={st.get('leads_dropped', 0)}")
    print(f"LEADS: {total} new   personal={conf['published_personal']}  generic={conf['published_generic']}  guessed={conf['guessed']}  "
          f"sites_with_leads={len(domains_)}")
    if total:
        print(f"published_personal share: {conf['published_personal'] / total:.0%} of all, "
              f"{conf['published_personal'] / max(conf['published_personal'] + conf['published_generic'], 1):.0%} of published")
    print(f"csv: {config.exports_dir() / ('leads-run-%d.csv' % run['id'])}")
    if args.show:
        for r in rows[: args.show]:
            print(f"  {r['email_confidence'][:9]:9} {r['email']:34} {r['name'][:22]:22} {r['role'][:30]:30} {r['company'][:24]}")
    return 0 if final.get("state") == "succeeded" else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="backend.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover", help="plan and run a lead search")
    d.add_argument("request")
    d.add_argument("--mode", choices=[m.value for m in Mode])
    d.add_argument("--max-domains", type=int, default=None)
    d.add_argument("--overpass", action="store_true", help="also query OpenStreetMap (service mode)")
    d.add_argument("--show", type=int, default=25, help="print this many leads")
    d.set_defaults(fn=discover)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
