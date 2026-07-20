# Stage 3 shadow monitor

Stage 3 is a read-only collection and research-alert layer. It is not a promoted predictive model. The Alpaca/IEX historical dataset remains blocked for empirical modelling because the quality audit found unresolved coverage/session limitations and the feed does not provide consolidated quotes, complete halt/float history, or a survivorship-safe universe.

The monitor writes append-only timestamped raw records, normalized market and catalyst records, gated market features, research-only alerts, heartbeats, and later outcome records. Alerts contain no probability, profitability claim, position instruction, or execution recommendation. Outcomes have `used_for_training=false`.

## Modes

- `synthetic`: deterministic offline source for tests and operational rehearsal.
- `replay`: replays an explicitly supplied JSON cache without network calls.
- `sec`: polls SEC EDGAR submissions using a configured CIK map and identifying User-Agent. It is read-only and begins with filings only; halts and live market bars remain separate follow-on providers.

No live network adapter is enabled by default. A future live adapter must use only `data.alpaca.markets`, `sec.gov`, and explicitly approved news domains. Trading, paper-trading, brokerage, account, and order endpoints are rejected by policy.

To run the SEC-first monitor, copy `config/shadow_sec.sample.json`, replace the placeholder contact address, set a small CIK map, and run `live-monitor --config config/shadow_sec.sample.json`. SEC automated access requires an identifying User-Agent; no API key is used.

SEC submissions responses are retained as immutable raw records. Relevant accession numbers are stored in a local append-only state checkpoint for restart-safe deduplication. Requests are paced at no more than ten per second by default. The submissions endpoint exposes filing/acceptance metadata; the provider does not scrape filing text.

The optional `sec_alpaca` mode composes SEC with a GET-only Alpaca snapshots provider. IEX is explicitly non-consolidated; SIP is marked consolidated only when configured and entitled. Quotes, halt status, float, and relative-volume history remain explicit missing flags when unavailable. Market observations are joined to same-cycle SEC events by normalized ticker, and the alert preserves the first observed price while later 5/15/30/60-minute outcomes remain append-only and excluded from training.

The `sec_alpaca_halts` mode adds the official Nasdaq Trader RSS feed. Polling is limited to once per minute. Raw XML and normalized halt reason, halt time, quote-resumption time, trade-resumption time, first-seen time, and processing time are preserved. Halt records use deterministic IDs and an on-disk seen registry; normalized writes remain immutable across restarts. A current feed supplies `halted` or `not_halted`; a stale feed never silently supplies either state.

## Git Bash

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -e .
live-monitor --config config/shadow_monitor.sample.json
```

Press Ctrl+C to stop. For a finite offline smoke test:

```bash
live-monitor --config config/shadow_monitor.sample.json --max-cycles 2
```

Records are immutable. Reusing an identifier with different content fails closed. Missing bars, quotes, halt status, float, consolidated coverage, and stale feeds are explicit quality flags; the monitor does not generate market features unless every required observation is present.

## Current blockers

- IEX is not consolidated market coverage.
- Point-in-time float, halt status, and survivorship-safe universe coverage are unavailable in the current Stage 2 sample.
- A production SEC poller requires an identifying User-Agent and monitored CIK mapping.
- Company-news collection requires an approved/licensed provider and a reviewed domain allowlist.
- The command therefore defaults to synthetic mode; replay and SEC are the other enabled adapters. Nasdaq halts, live Alpaca bars, licensed news, and social providers remain staged follow-on work. This is an operational collection scaffold, not evidence for modelling.
