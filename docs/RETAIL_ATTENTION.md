# Retail-Attention Research Pipeline

Status: deterministic engineering slice. It measures attention and never recommends, places, or manages a trade.

## Current implementation boundary

The module consumes explicit normalized inputs through a replaceable provider protocol. It does **not** scrape Reddit, Stocktwits, X, YouTube, TikTok, forums, Google Trends, or any other site. The only bundled provider is a plainly labelled synthetic fixture. A strict JSONL-directory adapter accepts an export only after its collector has declared the access method, governing terms URL, coverage interval, and permissions for text and account metrics.

No platform credential is required for the fixture or unit tests. Credentials become genuinely required only after the team selects an official API, confirms its current terms and university/team use rights, and implements that provider adapter.

Supported source labels are Reddit, Stocktwits, public X, YouTube, public TikTok, public trading forums, Google Trends, and other public sources. A label is schema support, not a claim that lawful automated access currently exists.

## Access and storage gate

Every source must declare:

- access method: approved API, public feed, licensed export, manual research export, or engineering fixture;
- whether collection authorization has been confirmed;
- terms URL and review time for every non-fixture source;
- the enforced API/feed rate-limit policy (or explicit no-network/manual status);
- collection coverage start and end;
- whether text analysis is permitted;
- storage mode: none, hash only, approved excerpt, or approved full text;
- whether author/account metrics may be processed;
- an explicit coverage limitation.

The pipeline fails closed if a source is undeclared or unauthorized, text exceeds declared rights, author metrics are supplied without permission, timestamps are inconsistent, or hashes and source records are invalid. It never bypasses a login, robots restriction, rate limit, or platform control because no web collector exists in this slice.

Author identifiers are expected to be stable source-scoped pseudonymous keys. Raw profile names and unnecessary personal data are outside this contract. Full post text should not be exported when terms permit only metadata or derived analysis.

## Time and leakage semantics

`published_at`, `first_seen_at`, `ingested_at`, and `available_at` are preserved in UTC. A mention may enter a signal only when both `published_at <= as_of` and `available_at <= as_of`.

Interval counts use `available_at`, not a backfilled publication time. This prevents a post found late from creating an artificial historical spike. `first_observed_mention_time` is the earliest eligible `first_seen_at` in the supplied history.

An absent mention is treated as zero only when every declared source confirms collection coverage for that entire interval. If collection starts late, ends early, or changes during the baseline, the adjusted score is null and the output includes `INCOMPLETE_OR_CHANGING_BASELINE_SOURCE_COVERAGE`.

## Measurements

For each monitored stable security ID/ticker pair, including tickers with zero mentions, the pipeline emits:

- counts for each baseline interval and the current interval;
- current raw count, preceding count, per-hour velocity, and per-hour-squared acceleration;
- a bounded baseline-adjusted mention score;
- known unique-author count, coverage, and independent-author score;
- engagement velocity derived only from timestamped snapshots available by `as_of`;
- transparent lexicon sentiment and its text coverage;
- normalized provider-supplied account-quality average only where permitted, with coverage;
- original/repost ratio and coverage;
- copied/coordinated-language ratio using rule-based token-set similarity;
- promotional-language score;
- source counts, Herfindahl concentration, and diversity score;
- primary catalyst links, source supporting links, flags, and data-completeness warnings;
- an attention stage: `early`, `expanding`, `crowded`, `collapsing`, `quiet`, or `insufficient_data`.

Scores are descriptive rules, not calibrated probabilities and not evidence of predictive value. Thresholds are versioned configuration and must be frozen before out-of-sample evaluation.

## Downgrade rules

The transparent promotion score adds bounded penalties for:

- phrases such as “guaranteed squeeze”, “cannot lose”, “risk free”, “buy now”, or “100x”;
- language similar to a prior post inside the configured coordination lookback;
- a declared affiliate or paid-promotion flag;
- high observed engagement without a linked verified primary catalyst;
- a post made after a configured large return had already occurred.

The last rule uses only a timestamped price observation at or before the post and already available when the mention became available. It is deliberately called `AFTER_LARGE_OBSERVED_MOVE`: a live feature cannot know that “most of the eventual move” has happened without looking into the future. Ex-post total-move comparisons may be used only as later evaluation labels, never as contemporaneous features.

Copied language reduces the independent-author score even when different author keys repeat it. Concentrated attention, repost-heavy activity, and one-promoter activity influence the stage and flags. An unlinked post is never upgraded to confirmed company news.

## Normalized JSONL provider

Set `provider` to `jsonl_directory` and `provider_path` to a local directory. Required files are:

- `metadata.json`: provider, dataset kind, fetch time, monitored securities, source access declarations, and notes;
- `mentions.jsonl`: immutable normalized mention records and content hashes.

Optional files are:

- `catalysts.jsonl`: point-in-time catalyst references with a primary-source flag;
- `price_context.jsonl`: point-in-time returns from an explicit historical reference.

The directory adapter makes no network requests and does not establish that an upstream export was lawful; it verifies the collector's explicit declarations and normalized data consistency. Raw/licensed platform data must remain outside Git.

## Sample

```powershell
python -m pip install -e .
retail-attention-sample --config config/retail_attention.sample.json
```

The ignored output directory contains `attention_signals.jsonl`, `quality_report.json`, and `run_manifest.json`. Every fixture row uses a `fixture://` link and the manifest states that it is not social or market data.

## Unresolved data and licensing requirements

- No official live API or public-feed adapter has been selected.
- Reddit OAuth credentials are needed only if the team approves and builds a current-terms-compliant Reddit adapter.
- Stocktwits, X, YouTube/transcripts, TikTok, forum, and trend access, quotas, automated-use rights, derived-data rights, and retention/display rights remain unconfirmed.
- Historical post and engagement-snapshot archives suitable for unbiased backtesting are unavailable in the repository.
- Cross-platform author identity is intentionally unsupported; unique-author measures are source-scoped and cannot prove real-world independence.
- Account-quality fields are incomparable across providers unless a versioned normalization is designed and validated.
- Deleted/private content, edits, bot labels, view-count definitions, recommendation algorithms, and incomplete search/index coverage can bias results.
- No predictive or profitable claim is supported until chronological out-of-sample evaluation joins immutable attention signals to later outcomes.

## Recommended next task

Select one official source after a written terms review, then implement a rate-limited, cached adapter with injected transport and offline contract tests. Reddit is the currently documented optional first candidate in the architecture, but no selection or credential request should occur until access and retention rights are reconfirmed.
