"""Deterministic, explicitly synthetic shadow source."""

from __future__ import annotations

from datetime import timedelta

from equity_research.catalyst_intelligence.contracts import SourceBatch, SourceDocument, SourceKind

from .contracts import MarketObservation, MonitorMode, RawSourceItem, ShadowInputBatch, SourceFamily


class SyntheticShadowProvider:
    def __init__(self) -> None:
        self._cycle = 0
        self._first_seen = None

    def poll(self, processing_time):  # type: ignore[no-untyped-def]
        self._cycle += 1
        if self._first_seen is None:
            self._first_seen = processing_time
        source_time = processing_time - timedelta(seconds=5)
        price = 10.0 + self._cycle * 0.1
        market_url = "fixture://synthetic/market/SYN"
        sec_url = "fixture://synthetic/sec/0000000000-26-000001"
        raw_market = RawSourceItem(
            f"synthetic-market-{self._cycle}", SourceFamily.MARKET_DATA, market_url,
            source_time, processing_time, processing_time,
            {"ticker": "SYN", "close": price, "volume": 1000 + self._cycle * 100}, "synthetic_only",
        )
        raw_sec = RawSourceItem(
            "synthetic-sec-1", SourceFamily.SEC, sec_url, self._first_seen - timedelta(seconds=5),
            self._first_seen, processing_time, {"form": "8-K", "title": "Synthetic contract announcement"}, "synthetic_only",
        )
        observation = MarketObservation(
            f"SYN-{source_time.strftime('%Y%m%dT%H%M%S')}", "synthetic-security-SYN", "SYN",
            market_url, source_time, processing_time, processing_time, "synthetic", True,
            price, 1000 + self._cycle * 100, price - 0.01, price + 0.01, True, "not_halted", 1_000_000,
        )
        document = SourceDocument(
            "synthetic-sec-document-1", "SYN", "synthetic-issuer-SYN",
            "Synthetic issuer reports material contract", "The issuer entered a $5 million purchase contract.",
            sec_url, SourceKind.SEC_FILING, self._first_seen - timedelta(seconds=5), self._first_seen - timedelta(seconds=5), self._first_seen,
            self._first_seen, self._first_seen, True, accession_number="0000000000-26-000001", form_type="8-K",
        )
        return ShadowInputBatch(
            "synthetic-shadow-v1", MonitorMode.SYNTHETIC, processing_time,
            (raw_market, raw_sec), (observation,),
            SourceBatch("synthetic-shadow-v1", "synthetic_shadow", processing_time, (document,)),
            ((SourceFamily.MARKET_DATA, source_time), (SourceFamily.SEC, source_time)),
        )
