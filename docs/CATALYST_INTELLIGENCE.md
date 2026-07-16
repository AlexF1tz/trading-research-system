# Catalyst Intelligence

Implemented 16 July 2026. The current output is deterministic decision support, not a claim that a catalyst will move a security or produce a profitable trade.

## Scope

The pipeline discovers normalized source documents through replaceable providers, validates their provenance and time ordering, then produces immutable classified events. It prioritizes:

1. SEC filings;
2. official regulator announcements;
3. exchange announcements;
4. company investor-relations releases;
5. secondary news that cites an already-available primary document;
6. unverified secondary or social claims.

The current code includes a strict JSONL-directory adapter and a composite adapter. It does not make live SEC, IR, exchange, regulator, news, or social network calls and does not assume credentials or redistribution rights.

## Timestamp contract

Each `SourceDocument` retains:

- `published_at`: timestamp declared by the source;
- `first_public_at`: earliest verified public-source timestamp;
- `first_seen_at`: first observation by this system/provider export;
- `ingested_at`: local persistence time;
- `available_at`: conservative cutoff permitted in features;
- `source_timestamp_verified`: whether the adapter can defend the source timestamp.

For an unverified timestamp, `available_at` cannot precede `first_seen_at`. Later primary corroboration never retroactively upgrades an earlier secondary event. A new primary event is recorded at its own availability time instead.

All timestamps must be timezone-aware UTC. Source URLs and source-native IDs are preserved. Invalid orderings fail the pipeline by default.

## Source verification

| Source | Tier | Verification policy |
| --- | --- | --- |
| SEC, regulator, exchange, company IR | Primary | `confirmed_primary` content, with a separate warning if its timestamp cannot be verified |
| Secondary news linked to an already-available primary document | Secondary | `corroborated` |
| Secondary without earlier primary evidence | Secondary | `unverified` |
| Social/unverified source | Secondary | Always `unverified`; always classified as an unverified rumour and direction `ambiguous` |

An SEC-labelled non-fixture URL must be on `sec.gov`. Real adapters should add allowlists or issuer registries appropriate to IR, exchange, and regulator sources rather than trusting arbitrary provider labels.

## Classification taxonomy

The rule baseline covers:

- earnings and guidance;
- contracts and purchase orders;
- partnerships;
- mergers and acquisitions;
- FDA and clinical events;
- litigation and court outcomes;
- product launches;
- management changes;
- insider transactions;
- offerings, ATM programmes, warrants and dilution;
- reverse splits;
- unverified rumours;
- other or uncertain.

Rules use auditable form types, filing items, and phrases. A primary category is selected by fixed precedence and additional matches are retained as related categories. The classifier stores matched evidence such as `form:424B5`, `form_item:5.02`, or `phrase:purchase order`.

Direction is separately derived as positive, negative, or ambiguous. Promotional adjectives alone cannot create a positive direction. Transaction-role ambiguity, partnerships without economics, and unsupported product claims remain ambiguous.

## Numerical and dilution details

The baseline extracts and preserves the exact matched text plus normalized values for:

- USD amounts and EPS-like amounts;
- percentages;
- share counts;
- split ratios.

Adapter-supplied structured numerical details take precedence and are deduplicated with text extractions. Extraction is deliberately narrow; it does not infer contract term, backlog quality, non-GAAP comparability, trial statistics, or fully diluted share count.

Dilution risk is rule-based:

- active ATM, registered-direct, public/private offering, or warrant exercise: high;
- shelf registration without an identified takedown: medium;
- reverse split: medium because it may precede financing, while remaining distinct from actual issuance;
- no observed dilution evidence: none, not proof that no dilution exists.

## Novelty, materiality, and quality flags

Novelty and materiality are transparent 0–100 heuristics, not calibrated probabilities.

- Exact/near repeats receive novelty 5 and `REPEATED_ANNOUNCEMENT`.
- A document first discovered more than the configured delay after its public timestamp receives `STALE_NEWS`.
- Two or more promotional phrases without extracted material numbers receive `PROMOTIONAL_WITHOUT_MATERIAL_NUMBERS` and a materiality penalty.
- Unverified rumours have materiality capped at 15.
- Primary evidence, numerical detail, and large disclosed monetary values can raise materiality.

The system records all documents and flags rather than dropping unsuccessful or rejected candidates.

## Output contract

Every event includes the requested fields:

- ticker and first-public timestamp;
- source URL, source kind, and primary/secondary tier;
- verification status;
- catalyst and related categories;
- direction;
- novelty and materiality scores;
- numerical details;
- dilution risk;
- expected future catalyst date when explicitly supplied or found in a narrow ISO-date pattern;
- concise category-specific bull and failure cases;
- stale, repeated, promotional, unverified, timestamp, and dilution flags.

Bull/failure text is a fixed risk template. It does not introduce facts that are absent from the source.

## Working sample

```powershell
python -m pip install -e .
catalyst-sample --config config/catalyst.sample.json --output-dir output/catalyst_sample
```

The deterministic fixture creates 14 synthetic documents and 14 event records covering every requested category. It intentionally includes one stale document, one repeated release, two promotional/no-number announcements, one high-dilution ATM event, and one unverified social rumour.

Outputs are ignored local files:

- `events.jsonl` — one immutable event per source document;
- `quality_report.json` — timestamp/provenance errors and warnings;
- `run_manifest.json` — counts, limitations, and fixture status.

The manifest and every fixture URL state that the data is synthetic and is not company news.

## Real-data requirements

- SEC ingestion can use free EDGAR APIs/RSS with a declared user agent and rate controls.
- Company IR feeds require issuer-specific allowlists, parsing, and storage/robots review.
- Exchange and regulator announcements need source-specific adapters and authoritative timestamp fields.
- Secondary news requires licensed API/history and explicit headline/text storage rights.
- Historical novelty testing needs first-seen archives; current pages alone cannot reconstruct when a repeated release first appeared.
- Rumour inputs remain optional. The separate retail-attention module does not scrape platforms and cannot promote an unverified social post to confirmed company news.
