# Coproxy Load Test Results

Date: 2026-03-20
Server: VPS 1 vCPU / 1 GB RAM / Ubuntu 22.04
TPM limit: 60,000 tokens/minute
Model: gpt-4o-mini (ChatGPT Plus subscription via OAuth)

---

## Light Load Test (`loadtest.py`)

20 requests with mixed priorities in bursts over ~2.5 minutes.
Small prompts, max_tokens 10–200.

### Configuration
- 5 high / 9 normal / 6 low priority requests
- 7 bursts, 15–30s between bursts
- max_tokens: 10–200 per request

### Results

```
Total time:      139.7s
Requests:        20 OK / 0 failed / 20 total
Total tokens:    881

Latency avg:     1.5s
Latency p50:     1.3s
Latency p95:     4.2s
Latency max:     4.2s

Priority breakdown:
  [  high] 5 reqs, avg 1.2s, tokens 112
  [normal] 9 reqs, avg 1.5s, tokens 377
  [   low] 6 reqs, avg 1.9s, tokens 392
```

### Proxy Stats After Test

```
TPM utilization:   0.5% (peak ~1%)
Queue depth:       0 (max 0 during this test)
Wait time avg:     0.0s
Timeouts:          0
```

### Observations
- No queue contention at all — requests are too small to fill the 60K TPM window
- Priority ordering visible: high avg 1.2s < normal 1.5s < low 1.9s
- All 20/20 requests succeeded

---

## Stress Test (`loadtest_stress.py`)

20 heavy requests fired in 4 rapid waves to saturate the TPM budget.
Each request: max_tokens=4096 (~4,600–5,500 estimated tokens).

### Configuration
- 5 high / 8 normal / 7 low priority requests
- 4 waves: 5 requests each at t=0s, 5s, 10s, 15s
- max_tokens: 4096 per request (forces queue contention)

### Results

```
Total time:       49.0s
Requests:         18 OK / 2 rate-limited (429) / 0 errors
Total tokens:     19,210

Latency avg:      25.5s
Latency p50:      24.4s
Latency p95:      39.0s
Latency max:      39.0s

Priority breakdown:
  [  high]  5 OK  avg=20.5s  p50=20.7s  max=25.2s  tokens=4,872
  [normal]  8 OK  avg=25.3s  p50=24.4s  max=34.3s  tokens=8,482
  [   low]  5 OK  avg=31.0s  p50=32.5s  max=39.0s  tokens=5,856
  [   low]  2 rate-limited (429) — dropped at wave 4 (TPM at 93%)
```

### Proxy Stats After Test

```
TPM limit:        60,000
TPM peak util:    96.7% (at wave 3+4 overlap)
Queue max depth:  7
Queue wait avg:   1.72s
Queue wait max:   19.37s
Queue wait p50:   0.0s
Queue wait p95:   16.08s
Queue wait p99:   17.77s
Timeouts (429):   0 (proxy-side; the 2 rate-limits came from OpenAI)
```

### TPM Utilization Timeline

```
t=  0s  [░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░]  0.5%   Wave 1 sent
t=  3s  [███████████░░░░░░░░░░░░░░░░░░░░] 39.1%   Wave 1 processing
t=  6s  [███████████████████████░░░░░░░░░] 77.5%   Wave 2 sent
t= 12s  [███████████████████████████░░░░░] 93.0%   Wave 3 sent, queue=3
t= 15s  [████████████████████████████░░░░] 96.7%   Wave 4 sent, 2 low rejected
t= 24s  [████████████████████████████░░░░] 94.4%   Waves completing
t= 33s  [█████████████████████░░░░░░░░░░░] 72.8%   Draining
t= 48s  [███████████░░░░░░░░░░░░░░░░░░░░░] 37.5%   Almost done
t= 49s  Done — last request (k8s-low) completes
```

### Key Observations

1. **Priority system works correctly**: high avg 20.5s < normal 25.3s < low 31.0s
2. **Greedy best-fit dispatch**: high-priority requests are dispatched first when TPM budget frees up
3. **Graceful degradation**: at 93%+ utilization, only the lowest-priority requests (wave 4 low) are rate-limited
4. **No proxy-side timeouts**: all queue management worked within the 120s timeout window
5. **Real token usage much lower than estimated**: 19,210 actual vs ~100K estimated (max_tokens=4096 but model generates much less)

---

## Previous Test (TPM=30K, before optimization)

Before raising TPM from 30K to 60K, the same stress test showed:
- Queue wait max: **57s** (requests stuck waiting for TPM budget)
- High-priority requests delayed by 20-30s in queue alone
- Total test time: ~120s (vs 49s with 60K)

The TPM increase to 60K eliminated most queue contention while still providing fair scheduling under heavy load.
