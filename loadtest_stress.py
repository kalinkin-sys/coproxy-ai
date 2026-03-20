#!/usr/bin/env python3
"""
Coproxy STRESS test — 20 heavy requests fired in rapid bursts over ~30s
to saturate the 30K TPM budget and force queue contention.

Usage:
    python3 loadtest_stress.py [--base-url URL] [--secret SECRET]
"""

import argparse
import asyncio
import os
import sys
import time

import httpx

# ── Heavy scenarios: max_tokens=4096 to eat TPM budget fast ────────
# estimate_total ≈ len(prompt)/3 + 500 + max_tokens
# So each request reserves ~4600-5500 tokens
# With 30K TPM limit, only ~6 fit in the window at once → the rest queue

SCENARIOS = [
    # (priority, model, prompt, max_tokens, label)
    # Wave 1: 5 requests at t=0 (instant burst)
    ("high",   "gpt-4o-mini", "Write a detailed essay about the history of the internet. Cover ARPANET, TCP/IP, the World Wide Web, and modern developments.", 4096, "essay-high"),
    ("high",   "gpt-4o-mini", "You are a senior Python developer. Review this code and suggest improvements: def fib(n): return n if n<2 else fib(n-1)+fib(n-2)", 4096, "review-high"),
    ("normal", "gpt-4o-mini", "Write a comprehensive comparison of Python, JavaScript, and Rust. Cover syntax, performance, ecosystem, use cases, and learning curve.", 4096, "compare-norm"),
    ("normal", "gpt-4o-mini", "Explain the differences between REST, GraphQL, and gRPC. Include examples, pros/cons, and when to use each.", 4096, "api-normal"),
    ("low",    "gpt-4o-mini", "Write a tutorial on Docker containers for beginners. Cover images, volumes, networking, docker-compose, and best practices.", 4096, "docker-low"),

    # Wave 2: 5 more at t=5s (before wave 1 finishes)
    ("high",   "gpt-4o-mini", "Explain quantum computing to a software engineer. Cover qubits, superposition, entanglement, quantum gates, and practical applications.", 4096, "quantum-high"),
    ("normal", "gpt-4o-mini", "Write a detailed guide on database indexing. Cover B-trees, hash indexes, composite indexes, partial indexes, and query optimization.", 4096, "db-index-norm"),
    ("normal", "gpt-4o-mini", "Explain microservices architecture. Cover service discovery, API gateways, circuit breakers, event sourcing, and CQRS.", 4096, "micro-normal"),
    ("low",    "gpt-4o-mini", "Write a comprehensive guide to Linux system administration. Cover users, permissions, systemd, networking, and security.", 4096, "linux-low"),
    ("low",    "gpt-4o-mini", "Explain machine learning algorithms. Cover linear regression, decision trees, neural networks, SVMs, and ensemble methods.", 4096, "ml-low"),

    # Wave 3: 5 at t=10s (queue should be building up)
    ("high",   "gpt-4o-mini", "Design a real-time chat application architecture. Cover WebSockets, message queues, presence detection, typing indicators, and scaling.", 4096, "chat-high"),
    ("normal", "gpt-4o-mini", "Write about cryptography fundamentals. Cover symmetric/asymmetric encryption, hashing, digital signatures, TLS, and PKI.", 4096, "crypto-normal"),
    ("normal", "gpt-4o-mini", "Explain OAuth 2.0 and OpenID Connect in detail. Cover all grant types, PKCE, token refresh, scopes, and security considerations.", 4096, "oauth-normal"),
    ("low",    "gpt-4o-mini", "Write a guide to Kubernetes. Cover pods, services, deployments, ConfigMaps, secrets, ingress, and horizontal pod autoscaling.", 4096, "k8s-low"),
    ("low",    "gpt-4o-mini", "Explain data structures and their time complexities. Cover arrays, linked lists, trees, graphs, hash tables, and heaps.", 4096, "ds-low"),

    # Wave 4: 5 at t=15s (maximum pressure)
    ("high",   "gpt-4o-mini", "Design a payment processing system. Cover PCI compliance, idempotency, reconciliation, fraud detection, and multi-currency support.", 4096, "payment-high"),
    ("normal", "gpt-4o-mini", "Write about CI/CD pipelines. Cover Git workflows, automated testing, Docker builds, blue-green deployments, and canary releases.", 4096, "cicd-normal"),
    ("normal", "gpt-4o-mini", "Explain distributed systems concepts. Cover CAP theorem, consensus algorithms, consistent hashing, vector clocks, and CRDTs.", 4096, "distrib-norm"),
    ("low",    "gpt-4o-mini", "Write a guide to web performance optimization. Cover Core Web Vitals, lazy loading, CDNs, caching strategies, and code splitting.", 4096, "perf-low"),
    ("low",    "gpt-4o-mini", "Explain functional programming concepts. Cover pure functions, immutability, monads, functors, higher-order functions, and currying.", 4096, "fp-low"),
]

# Wave schedule: (delay_seconds, start_idx, end_idx)
WAVES = [
    (0,  0,  5),   # Wave 1: instant
    (5,  5,  10),  # Wave 2: 5s later
    (10, 10, 15),  # Wave 3: 10s
    (15, 15, 20),  # Wave 4: 15s
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
    test_start: float,
) -> dict:
    """Send one chat completion request and return timing info."""
    queued_at = time.monotonic() - test_start
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
            timeout=300.0,
        )
        elapsed = time.monotonic() - t0
        finished_at = time.monotonic() - test_start
        status = resp.status_code
        tokens = 0
        if status == 200:
            data = resp.json()
            tokens = data.get("usage", {}).get("total_tokens", 0)
        elif status == 429:
            tokens = -1  # rate limited

        pcolor = {"high": "\033[91m", "normal": "\033[93m", "low": "\033[94m"}
        reset = "\033[0m"
        c = pcolor.get(priority, "")
        status_icon = "OK" if status == 200 else f"\033[91m{status}\033[0m"
        print(f"  {c}{priority[0].upper()}{reset} #{idx:02d} {label:<14s} "
              f"sent@{queued_at:5.1f}s done@{finished_at:5.1f}s "
              f"{status_icon:>3s} {elapsed:5.1f}s {tokens:5d}tok")
        return {
            "idx": idx, "label": label, "priority": priority,
            "status": status, "tokens": tokens, "elapsed": round(elapsed, 2),
            "queued_at": round(queued_at, 2), "finished_at": round(finished_at, 2),
        }

    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"  ! #{idx:02d} {label:<14s} ERROR {elapsed:.1f}s  {e}")
        return {
            "idx": idx, "label": label, "priority": priority,
            "status": 0, "tokens": 0, "elapsed": round(elapsed, 2),
            "queued_at": round(queued_at, 2), "finished_at": 0,
        }


async def poll_stats(client: httpx.AsyncClient, base_url: str, secret: str,
                     test_start: float, stop_event: asyncio.Event):
    """Periodically print queue/TPM status during the test."""
    while not stop_event.is_set():
        try:
            resp = await client.get(
                f"{base_url}/v1/stats",
                headers={"Authorization": f"Bearer {secret}"},
                timeout=5.0,
            )
            if resp.status_code == 200:
                s = resp.json()
                t = time.monotonic() - test_start
                bar_len = 30
                util = s.get("tpm_utilization_pct", 0)
                filled = int(bar_len * min(util, 100) / 100)
                bar = "\033[92m" + "█" * filled + "\033[90m" + "░" * (bar_len - filled) + "\033[0m"
                print(f"\n  ┌ t={t:5.1f}s  TPM [{bar}] {util}%  "
                      f"used={s.get('tpm_used',0)}/{s.get('tpm_limit',0)}  "
                      f"queue={s.get('queue_depth',0)} (max={s.get('queue_max_depth',0)})")
                wt = s.get("wait_time", {})
                if wt.get("samples", 0) > 0:
                    print(f"  └ wait avg={wt['avg']}s max={wt['max']}s p95={wt['p95']}s  "
                          f"reqs={s.get('requests',{}).get('total',0)} "
                          f"timeouts={s.get('requests',{}).get('timeouts',0)}\n")
                else:
                    print(f"  └ no requests completed yet\n")
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3.0)
            break
        except asyncio.TimeoutError:
            pass


async def run_stress(base_url: str, secret: str):
    print(f"\n{'='*70}")
    print(f"  COPROXY STRESS TEST — 20 heavy requests in 4 rapid waves")
    print(f"  Target: {base_url}")
    print(f"  Each request: max_tokens=4096 (~4600-5500 estimated)")
    print(f"  TPM limit: 30000 → max ~6 concurrent in window")
    print(f"  Waves: 5 reqs @ 0s, 5s, 10s, 15s")
    h = sum(1 for p, *_ in SCENARIOS if p == "high")
    n = sum(1 for p, *_ in SCENARIOS if p == "normal")
    l = sum(1 for p, *_ in SCENARIOS if p == "low")
    print(f"  Priority mix: {h}H / {n}N / {l}L")
    print(f"{'='*70}\n")

    async with httpx.AsyncClient() as client:
        # Health check
        try:
            resp = await client.get(f"{base_url}/health", timeout=5.0)
            if resp.status_code != 200:
                print(f"Health check failed: {resp.status_code}")
                return
        except Exception as e:
            print(f"Cannot connect: {e}")
            return

        print(f"  [P] #   {'Label':<14s} {'Sent':>9s} {'Done':>9s} {'Status':>4s} {'Time':>5s} {'Tokens':>7s}")
        print(f"{'─'*70}")

        test_start = time.monotonic()
        stop_event = asyncio.Event()

        # Start stats poller
        poller = asyncio.create_task(
            poll_stats(client, base_url, secret, test_start, stop_event)
        )

        # Launch all waves
        all_tasks = []
        for delay, start, end in WAVES:
            await asyncio.sleep(max(0, delay - (time.monotonic() - test_start)))
            wave_num = WAVES.index((delay, start, end)) + 1
            print(f"\n  >>> WAVE {wave_num}: sending {end-start} requests <<<\n")
            for idx in range(start, end):
                priority, model, prompt, max_tokens, label = SCENARIOS[idx]
                task = asyncio.create_task(
                    send_request(client, base_url, secret, idx, priority,
                                 model, prompt, max_tokens, label, test_start)
                )
                all_tasks.append(task)

        # Wait for all to complete
        results = await asyncio.gather(*all_tasks)
        total_elapsed = time.monotonic() - test_start

        # Stop poller
        stop_event.set()
        await poller

        print(f"\n{'─'*70}")

        # Final stats from proxy
        await asyncio.sleep(1)
        try:
            resp = await client.get(
                f"{base_url}/v1/stats",
                headers={"Authorization": f"Bearer {secret}"},
                timeout=10.0,
            )
            stats = resp.json() if resp.status_code == 200 else {}
        except Exception:
            stats = {}

        # Summary
        ok = [r for r in results if r["status"] == 200]
        rate_limited = [r for r in results if r["status"] == 429]
        failed = [r for r in results if r["status"] not in (200, 429)]
        total_tokens = sum(r["tokens"] for r in ok)
        times = sorted(r["elapsed"] for r in ok)

        print(f"\n{'='*70}")
        print(f"  STRESS TEST RESULTS")
        print(f"{'='*70}")
        print(f"  Total time:       {total_elapsed:.1f}s")
        print(f"  Requests:         {len(ok)} OK / {len(rate_limited)} rate-limited / {len(failed)} error")
        print(f"  Total tokens:     {total_tokens}")
        if times:
            print(f"  Latency avg:      {sum(times)/len(times):.1f}s")
            print(f"  Latency p50:      {times[len(times)//2]:.1f}s")
            print(f"  Latency p95:      {times[int(len(times)*0.95)]:.1f}s")
            print(f"  Latency max:      {max(times):.1f}s")

        # Priority breakdown
        print()
        for p in ("high", "normal", "low"):
            pr = [r for r in ok if r["priority"] == p]
            if pr:
                pt = sorted(r["elapsed"] for r in pr)
                print(f"  [{p:>6s}] {len(pr):2d} OK  avg={sum(pt)/len(pt):5.1f}s  "
                      f"p50={pt[len(pt)//2]:5.1f}s  max={max(pt):5.1f}s  "
                      f"tokens={sum(r['tokens'] for r in pr)}")
            rl = [r for r in rate_limited if r["priority"] == p]
            if rl:
                print(f"  [{p:>6s}] {len(rl):2d} 429 (rate limited)")

        # Proxy stats
        if stats:
            print(f"\n{'='*70}")
            print(f"  PROXY-SIDE STATS")
            print(f"{'='*70}")
            print(f"  TPM limit:        {stats.get('tpm_limit')}")
            print(f"  TPM used now:     {stats.get('tpm_used')}")
            print(f"  TPM peak util:    {stats.get('tpm_utilization_pct')}%")
            print(f"  Queue max depth:  {stats.get('queue_max_depth')}")

            reqs = stats.get("requests", {})
            bp = reqs.get("by_priority", {})
            print(f"  Total requests:   {reqs.get('total')}")
            print(f"    high={bp.get('high',0)}  normal={bp.get('normal',0)}  low={bp.get('low',0)}")
            print(f"  Timeouts (429):   {reqs.get('timeouts', 0)}")

            tok = stats.get("tokens", {})
            print(f"  Tokens settled:   {tok.get('total_settled')}")
            print(f"  Avg per request:  {tok.get('avg_per_request')}")

            wt = stats.get("wait_time", {})
            print(f"  Queue wait avg:   {wt.get('avg')}s")
            print(f"  Queue wait max:   {wt.get('max')}s")
            print(f"  Queue wait p50:   {wt.get('p50')}s")
            print(f"  Queue wait p95:   {wt.get('p95')}s")
            print(f"  Queue wait p99:   {wt.get('p99')}s")

        print(f"\n{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Coproxy stress test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--secret", default=os.environ.get("COPROXY_SECRET", ""))
    args = parser.parse_args()

    if not args.secret:
        print("Error: pass --secret or set COPROXY_SECRET env var")
        sys.exit(1)

    asyncio.run(run_stress(args.base_url, args.secret))


if __name__ == "__main__":
    main()
