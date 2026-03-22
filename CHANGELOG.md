# Changelog

## [0.2.0] - 2026-03-22

### Added

- **Aggressive mode** (`COPROXY_TPM_AGGRESSIVE=true`): Sends requests directly to OpenAI without waiting in the TPM queue. Falls back to queue on 429. Reduces latency when TPM budget is available.
- **TPM auto-detect** (`COPROXY_TPM_AUTO_DETECT=true`): Probes OpenAI at startup to detect the actual org TPM limit from `x-ratelimit-limit-tokens` response headers. Costs ~20 tokens per startup.
- **Exchange logging** (`COPROXY_LOG_EXCHANGES=true`): Saves full request and response bodies to `~/.coproxy-logs/exchanges/` for debugging. Includes reassembled streaming responses. Auto-cleans to last 200 exchanges.
- **Security warnings**: Prominent warnings in logs and on disk when exchange logging is active. Periodic re-warnings every 5 minutes.
- **`/v1/stats` additions**: `aggressive_mode`, `exchange_logging` flags and `direct_hits` counter.
- **`coproxy-exchanges` CLI tool**: Viewer for exchange logs with `--full` and `--turn` filter options.
- **`record_direct()` method** on TPMDispatcher for tracking aggressive-mode token usage.

### Changed

- Exchange logging replaces the old `_log_request()` debug logging (which only saved request bodies to `/tmp`).
- `/health` endpoint now reports `aggressive_mode` and `exchange_logging` status.

### Configuration

New environment variables:

| Variable | Default | Description |
|---|---|---|
| `COPROXY_LOG_EXCHANGES` | `false` | Save full request+response to disk (debug only!) |
| `COPROXY_TPM_AGGRESSIVE` | `false` | Try direct send before queuing |
| `COPROXY_TPM_AUTO_DETECT` | `false` | Probe actual TPM limit at startup |

## [0.1.0] - 2026-03-20

Initial release.
