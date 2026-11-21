"""Run the four canonical evaluation prompts against a live CineContext server.

Usage:
    CINECONTEXT_TEST_URL=http://127.0.0.1:8765 python tests/eval_live.py
    python tests/eval_live.py --url http://127.0.0.1:8766 --auto-resubmit --output /tmp/live_eval.json
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

CASES = (
    ("medieval", "I'm interested in Kingdom Come: Deliverance II. Can you recommend a few films about medieval European history?"),
    ("romanian", "I'm interested in the Romanian New Wave. Which films should I watch?"),
    ("italian", "I'm interested in Italian neorealism. Which films should I watch?"),
    ("spiderman", "I'm getting ready to watch Spider-Man: Brand New Day. Please recommend four films to watch beforehand to catch up on the necessary background and story."),
)


def api_call(base_url: str, path: str, data=None, timeout: int = 600):
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api" + path,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def ensure_ready(base_url: str, *, auto_resubmit: bool, min_remaining: int = 750) -> dict:
    for _ in range(8):
        health = api_call(base_url, "/health", timeout=20)
        if health.get("model_mode") == "mock":
            raise RuntimeError("Server is running in demo (mock) mode. Use `python tests/smoke.py` for demo checks.")
        if health.get("model_mode") != "tacc":
            return health
        status = health.get("model_status")
        job = health.get("tacc_job") or {}
        job_id = job.get("id")
        remaining = job.get("remaining_seconds")
        if status == "ready" and (remaining is None or remaining >= min_remaining):
            return health
        if auto_resubmit and job_id and (status == "stopped" or (status == "ready" and remaining is not None and remaining < min_remaining)):
            restart = status == "ready"
            print(f"[tacc] {'Restarting' if restart else 'Submitting replacement after'} job {job_id}...", flush=True)
            try:
                sub = api_call(base_url, "/tacc/jobs", {"previous_job_id": job_id, "restart": restart}, timeout=300)
                print(f"[tacc] Submitted job {sub.get('job_id')}", flush=True)
            except urllib.error.HTTPError as error:
                print(f"[tacc] Submit returned HTTP {error.code}; retrying in 10s...", flush=True)
                time.sleep(10)
                continue
        while True:
            time.sleep(10)
            try:
                health = api_call(base_url, "/health", timeout=20)
            except Exception:
                continue
            status = health.get("model_status")
            job_id = (health.get("tacc_job") or {}).get("id")
            if status == "ready":
                return health
            if status == "stopped":
                if not auto_resubmit:
                    raise RuntimeError(f"TACC job {job_id} stopped before becoming ready.")
                print(f"[tacc] Job {job_id} stopped before ready; retrying...", flush=True)
                break
            print(f"[tacc] Waiting for model readiness: status={status} job={job_id}", flush=True)
    raise RuntimeError("TACC model did not reach ready state.")


def evaluate_case(base_url: str, name: str, prompt: str, *, auto_resubmit: bool) -> dict:
    health = ensure_ready(base_url, auto_resubmit=auto_resubmit)
    job_id = (health.get("tacc_job") or {}).get("id")
    started = time.time()
    print(f"\n=== [{name}] Parsing prompt ===", flush=True)
    parsed = api_call(base_url, "/requests/parse", {"prompt": prompt}, timeout=360)
    parse_seconds = time.time() - started
    constraints = parsed["constraints"]
    print(
        f"[{name}] Parsed in {parse_seconds:.1f}s | theme={constraints['theme']!r} | count={constraints['count']} "
        f"| setting_match={constraints['setting_match']} | story_period={constraints['story_period']!r}",
        flush=True,
    )

    run_id = api_call(base_url, "/runs", {"constraints": constraints, "submission_key": str(uuid4())}, timeout=30)["run_id"]
    print(f"[{name}] Run ID: {run_id}", flush=True)
    run_started = time.time()
    last_stage = None
    while True:
        result = api_call(base_url, f"/runs/{run_id}", timeout=30)
        stage = result.get("stage")
        if stage != last_stage:
            print(f"[{name}] [{time.time() - started:.0f}s] status={result['status']} stage={stage}", flush=True)
            last_stage = stage
        if result["status"] in {"completed", "partial", "failed", "interrupted"}:
            break
        time.sleep(5)

    run_seconds = time.time() - run_started
    total_seconds = time.time() - started
    study = result.get("study") or {}
    films = {film["id"]: film for film in (study.get("films") or [])}
    evidence = {item["id"]: item for item in (study.get("evidence") or [])}
    guide = result.get("guide") or {}
    viewing_order = [
        f"{films[fid]['title']} ({films[fid]['year']})"
        for fid in (guide.get("viewing_order") or [])
        if fid in films
    ]
    timeline_views = {axis: len(events) for axis, events in (study.get("timeline_views") or {}).items()}

    film_details = []
    editorial_reason_hits = 0
    for fg in (guide.get("film_guides") or []):
        fid = fg["film_id"]
        film = films.get(fid)
        if not film:
            continue
        refs = [evidence[eid] for eid in fg["reason"].get("evidence_ids", []) if eid in evidence]
        has_editorial_citation = any(ref.get("kind") == "editorial" for ref in refs)
        if has_editorial_citation:
            editorial_reason_hits += 1
        readings = api_call(base_url, f"/runs/{run_id}/films/{fid}/readings", timeout=20).get("readings") or []
        ratings = api_call(base_url, f"/films/{fid}/ratings", timeout=20).get("ratings") or []
        film_details.append({
            "title": film["title"],
            "year": film["year"],
            "reason": fg["reason"]["text"],
            "reason_citations": [
                {"kind": ref.get("kind"), "locator": ref.get("locator"), "title": ref.get("source_title")}
                for ref in refs
            ],
            "readings": [
                {"publication": r.get("publication"), "title": r.get("title"), "url": r.get("url"), "origin": r.get("origin")}
                for r in readings
            ],
            "ratings": [
                {"origin": r.get("origin"), "value": r.get("value"), "count": r.get("count")}
                for r in ratings
            ],
        })

    return {
        "case": name,
        "prompt": prompt,
        "tacc_job_id": job_id,
        "run_id": run_id,
        "url": f"{base_url.rstrip('/')}/research/{run_id}",
        "status": result["status"],
        "error": result.get("error"),
        "reference_match": parsed.get("reference_match"),
        "constraints": constraints,
        "requested_count": constraints["count"],
        "selected_count": len(viewing_order),
        "parse_seconds": round(parse_seconds, 1),
        "run_seconds": round(run_seconds, 1),
        "total_seconds": round(total_seconds, 1),
        "viewing_order": viewing_order,
        "timeline_views": timeline_views,
        "primer_titles": [note["title"] for note in (guide.get("common_primer") or [])],
        "introduction": guide.get("introduction", ""),
        "editorial_reason_hits": editorial_reason_hits,
        "film_details": film_details,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run live CineContext evaluation across the four canonical cases.")
    parser.add_argument("--url", default=os.environ.get("CINECONTEXT_TEST_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--cases", default="", help="Comma-separated subset of case keys: medieval,romanian,italian,spiderman")
    parser.add_argument("--auto-resubmit", action="store_true", help="Automatically request/restart a TACC job when stopped or <7 min left.")
    parser.add_argument("--output", default="", help="Optional path to write full JSON results.")
    args = parser.parse_args(argv)

    selected_keys = {key.strip() for key in args.cases.split(",") if key.strip()}
    selected_cases = [(k, p) for k, p in CASES if not selected_keys or k in selected_keys]
    if not selected_cases:
        raise SystemExit(f"No matching cases for --cases={args.cases!r}")

    records = []
    for name, prompt in selected_cases:
        record = evaluate_case(args.url, name, prompt, auto_resubmit=args.auto_resubmit)
        records.append(record)
        if args.output:
            Path(args.output).write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== LIVE EVALUATION SUMMARY ===", flush=True)
    failures = 0
    for rec in records:
        if rec["status"] != "completed" or rec["selected_count"] != rec["requested_count"]:
            failures += 1
        pubs = sorted({r["publication"] for f in rec["film_details"] for r in f["readings"] if r.get("publication")})
        rated = sum(1 for f in rec["film_details"] if f["ratings"])
        print(
            f"- {rec['case']}: {rec['status']} ({rec['selected_count']}/{rec['requested_count']}) | "
            f"{rec['total_seconds']}s | views={rec['timeline_views']} | "
            f"editorial_in_reason={rec['editorial_reason_hits']}/{rec['selected_count']} | "
            f"ratings={rated}/{rec['selected_count']} | publishers={pubs} | {rec['url']}",
            flush=True,
        )
        print(f"  Viewing order: {' -> '.join(rec['viewing_order'])}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
