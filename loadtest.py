#!/usr/bin/env python3
"""
Coproxy load test — sends 20 requests with mixed priorities over ~3 minutes.

Usage:
    python3 loadtest.py [--base-url URL] [--secret SECRET]

Default: http://127.0.0.1:8765 with COPROXY_SECRET from env.
"""

import argparse
import asyncio
import json
import os
import random
import sys
import time

import httpx

# ── Test scenarios: different sizes and priorities ──────────────────

SCENARIOS = [
    # (priority, model, prompt, max_tokens, description)
    ("high",   "gpt-4o-mini", "Say hello in one word.",                  50,   "tiny-high"),
    ("high",   "gpt-4o-mini", "What is 2+2? Answer with just a number.", 10,   "minimal-high"),
    ("normal", "gpt-4o-mini", "List 3 colors.",                          100,  "small-normal"),
    ("normal", "gpt-4o-mini", "Explain what HTTP is in 2 sentences.",    200,  "medium-normal"),
    ("normal", "gpt-4o-mini", "Write a haiku about programming.",        100,  "haiku-normal"),
    ("low",    "gpt-4o-mini", "Count from 1 to 10.",                     100,  "count-low"),
    ("low",    "gpt-4o-mini", "What day of the week is it? One word.",   20,   "tiny-low"),
    ("high",   "gpt-4o-mini", "Translate 'hello' to French, Spanish, German. Short answers.", 150, "translate-high"),
    ("normal", "gpt-4o-mini", "What is the capital of France?",          50,   "geo-normal"),
    ("normal", "gpt-4o-mini", "Write a one-line joke.",                  100,  "joke-normal"),
    ("low",    "gpt-4o-mini", "Name 5 planets.",                         100,  "planets-low"),
    ("low",    "gpt-4o-mini", "What is Python?",                         200,  "python-low"),
    ("high",   "gpt-4o-mini", "Is water wet? Yes or no.",                10,   "yesno-high"),
    ("normal", "gpt-4o-mini", "Explain REST API in one sentence.",       100,  "rest-normal"),
    ("normal", "gpt-4o-mini", "What is JSON? Brief answer.",             100,  "json-normal"),
    ("low",    "gpt-4o-mini", "Write the alphabet backwards.",           200,  "alpha-low"),
    ("high",   "gpt-4o-mini", "What year is it?",                        10,   "year-high"),
    ("normal", "gpt-4o-mini", "Name 3 programming languages.",           50,   "langs-normal"),
    ("low",    "gpt-4o-mini", "What is 7 * 8?",                          10,   "math-low"),
    ("normal", "gpt-4o-mini", "Say 'test complete'.",                     20,   "final-normal"),
]


async def send_request(
    client: httpx.AsyncClient,
    base_url: str,
    secret: str,
    idx: int,
    priority: str,
    model: str,
    prompt: str,
    max_tokens: int,
    label: str,
) -> dict:
    """Send one chat completion request and return timing info."""
    t0 = time.monotonic()
    try:
        resp = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            },
            headers={
                "Authorization": f"Bearer {secret}",
                "X-Priority": priority,
            },
            timeout=180.0,
        )
        elapsed = time.monotonic() - t0
        status = resp.status_code
        tokens = 0
        answer = ""
        if status == 200:
            data = resp.json()
            tokens = data.get("usage", {}).get("total_tokens", 0)
            choices = data.get("choices", [])
            if choices:
                answer = choices[0].get("message", {}).get("content", "")[:60]
        else:
            answer = resp.text[:100]

        result = {
            "idx": idx,
            "label": label,
            "priority": priority,
            "status": status,
            "tokens": tokens,
            "elapsed": round(elapsed, 2),
            "answer": answer,
        }
        symbol = {"high": "\033[91mH\033[0m", "normal": "\033[93mN\033[0m", "low": "\033[94mL\033[0m"}
        print(f"  [{symbol.get(priority, '?')}] #{idx:02d} {label:<16s} {status} {elapsed:6.1f}s {tokens:5d}tok  {answer[:40]}")
        return result

    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"  [!] #{idx:02d} {label:<16s} ERROR {elapsed:.1f}s  {e}")
        return {
            "idx": idx, "label": label, "priority": priority,
            "status": 0, "tokens": 0, "elapsed": round(elapsed, 2),
            "answer": str(e)[:60],
        }


async def get_stats(client: httpx.AsyncClient, base_url: str, secret: str) -> dict:
    """Fetch /v1/stats from the proxy."""
    try:
        resp = await client.get(
            f"{base_url}/v1/stats",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=10.0,
        )
        return resp.json() if resp.status_code == 200 else {}
    except Exception:
        return {}


async def run_loadtest(base_url: str, secret: str):
    print(f"\n{'='*70}")
    print(f"  COPROXY LOAD TEST — 20 requests over ~3 minutes")
    print(f"  Target: {base_url}")
    print(f"  Priority mix: {sum(1 for p,*_ in SCENARIOS if p=='high')}H / "
          f"{sum(1 for p,*_ in SCENARIOS if p=='normal')}N / "
          f"{sum(1 for p,*_ in SCENARIOS if p=='low')}L")
    print(f"{'='*70}\n")

    async with httpx.AsyncClient() as client:
        # Check health first
        try:
            resp = await client.get(f"{base_url}/health", timeout=5.0)
            if resp.status_code != 200:
                print(f"Health check failed: {resp.status_code}")
                return
            print(f"Health: OK\n")
        except Exception as e:
            print(f"Cannot connect to {base_url}: {e}")
            return

        # Pre-test stats
        stats_before = await get_stats(client, base_url, secret)

        # Schedule 20 requests spread over ~3 minutes (every 9 seconds)
        # But with bursts: send 3-4 at once, then pause
        schedule = []
        t = 0.0
        i = 0
        while i < len(SCENARIOS):
            burst = random.randint(2, 4)
            for _ in range(burst):
                if i >= len(SCENARIOS):
                    break
                schedule.append((t, i))
                i += 1
            t += random.uniform(15.0, 30.0)  # 15-30s between bursts

        total_time_est = schedule[-1][0] if schedule else 0
        print(f"Schedule: {len(schedule)} requests in {len(set(t for t,_ in schedule))} bursts")
        print(f"Estimated duration: ~{total_time_est:.0f}s\n")
        print(f"{'─'*70}")
        print(f"  [P] # {'Label':<16s} {'Code':>4s} {'Time':>6s} {'Tokens':>7s}  Answer")
        print(f"{'─'*70}")

        results = []
        test_start = time.monotonic()

        # Group by burst time
        bursts: dict[float, list[int]] = {}
        for t, idx in schedule:
            bursts.setdefault(t, []).append(idx)

        for burst_time in sorted(bursts.keys()):
            # Wait until burst time
            elapsed = time.monotonic() - test_start
            wait = burst_time - elapsed
            if wait > 0:
                # Show queue status while waiting
                stats = await get_stats(client, base_url, secret)
                if stats:
                    q = stats.get("queue_depth", 0)
                    used = stats.get("tpm_used", 0)
                    util = stats.get("tpm_utilization_pct", 0)
                    print(f"\n  ... waiting {wait:.0f}s | queue={q} tpm_used={used} ({util}%) ...\n")
                await asyncio.sleep(wait)

            # Send burst concurrently
            indices = bursts[burst_time]
            tasks = []
            for idx in indices:
                priority, model, prompt, max_tokens, label = SCENARIOS[idx]
                tasks.append(
                    send_request(client, base_url, secret, idx, priority, model, prompt, max_tokens, label)
                )
            burst_results = await asyncio.gather(*tasks)
            results.extend(burst_results)

        total_elapsed = time.monotonic() - test_start
        print(f"{'─'*70}\n")

        # Post-test stats
        await asyncio.sleep(1)
        stats_after = await get_stats(client, base_url, secret)

        # Summary
        ok = [r for r in results if r["status"] == 200]
        failed = [r for r in results if r["status"] != 200]
        total_tokens = sum(r["tokens"] for r in ok)
        times = [r["elapsed"] for r in ok]

        print(f"{'='*70}")
        print(f"  RESULTS SUMMARY")
        print(f"{'='*70}")
        print(f"  Total time:      {total_elapsed:.1f}s")
        print(f"  Requests:        {len(ok)} OK / {len(failed)} failed / {len(results)} total")
        print(f"  Total tokens:    {total_tokens}")
        if times:
            times.sort()
            print(f"  Latency avg:     {sum(times)/len(times):.1f}s")
            print(f"  Latency p50:     {times[len(times)//2]:.1f}s")
            print(f"  Latency p95:     {times[int(len(times)*0.95)]:.1f}s")
            print(f"  Latency max:     {max(times):.1f}s")

        # Priority breakdown
        for p in ("high", "normal", "low"):
            pr = [r for r in ok if r["priority"] == p]
            if pr:
                pt = [r["elapsed"] for r in pr]
                print(f"  [{p:>6s}] {len(pr)} reqs, avg {sum(pt)/len(pt):.1f}s, "
                      f"tokens {sum(r['tokens'] for r in pr)}")

        if failed:
            print(f"\n  Failed requests:")
            for r in failed:
                print(f"    #{r['idx']:02d} {r['label']} -> {r['status']} ({r['answer'][:50]})")

        # Proxy-side stats
        if stats_after:
            print(f"\n{'='*70}")
            print(f"  PROXY STATS (from /v1/stats)")
            print(f"{'='*70}")
            print(f"  Uptime:          {stats_after.get('uptime_seconds', '?')}s")
            print(f"  TPM limit:       {stats_after.get('tpm_limit', '?')}")
            print(f"  TPM used now:    {stats_after.get('tpm_used', '?')}")
            print(f"  TPM utilization:  {stats_after.get('tpm_utilization_pct', '?')}%")
            print(f"  Queue depth now: {stats_after.get('queue_depth', '?')}")
            print(f"  Queue max depth: {stats_after.get('queue_max_depth', '?')}")

            reqs = stats_after.get("requests", {})
            print(f"  Total requests:  {reqs.get('total', '?')}")
            bp = reqs.get("by_priority", {})
            print(f"    high:          {bp.get('high', 0)}")
            print(f"    normal:        {bp.get('normal', 0)}")
            print(f"    low:           {bp.get('low', 0)}")
            print(f"  Timeouts:        {reqs.get('timeouts', 0)}")

            tok = stats_after.get("tokens", {})
            print(f"  Tokens settled:  {tok.get('total_settled', '?')}")
            print(f"  Avg per request: {tok.get('avg_per_request', '?')}")

            wt = stats_after.get("wait_time", {})
            print(f"  Wait time avg:   {wt.get('avg', '?')}s")
            print(f"  Wait time max:   {wt.get('max', '?')}s")
            print(f"  Wait time p50:   {wt.get('p50', '?')}s")
            print(f"  Wait time p95:   {wt.get('p95', '?')}s")
            print(f"  Wait time p99:   {wt.get('p99', '?')}s")

        print(f"\n{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Coproxy load test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--secret", default=os.environ.get("COPROXY_SECRET", ""))
    args = parser.parse_args()

    if not args.secret:
        print("Error: pass --secret or set COPROXY_SECRET env var")
        sys.exit(1)

    asyncio.run(run_loadtest(args.base_url, args.secret))


if __name__ == "__main__":
    main()
