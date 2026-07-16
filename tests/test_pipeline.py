from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from equity_research.market_data.pipeline import MarketDataPipeline, PipelineConfig
from equity_research.market_data.provider import MarketDataProvider
from equity_research.market_data.quality import QualityConfig
from equity_research.market_data.sample import run_sample
from equity_research.market_data.synthetic import SyntheticFixtureProvider


UTC = timezone.utc


class PipelineTests(unittest.TestCase):
    def test_synthetic_provider_satisfies_replaceable_protocol(self) -> None:
        provider = SyntheticFixtureProvider()
        self.assertIsInstance(provider, MarketDataProvider)

    def test_synthetic_pipeline_has_no_quality_errors(self) -> None:
        result = MarketDataPipeline(
            SyntheticFixtureProvider(),
            PipelineConfig(
                quality=QualityConfig(datetime(2026, 7, 16, tzinfo=UTC))
            ),
        ).run()
        self.assertEqual(result.error_count, 0)
        self.assertGreater(len(result.features), 10_000)
        self.assertTrue(
            any(bar.bid is not None for bar in result.dataset.one_minute_bars)
        )
        self.assertTrue(
            any(instrument.is_delisted for instrument in result.dataset.instruments)
        )
        self.assertGreater(len(result.dataset.corporate_actions), 0)
        self.assertGreater(len(result.dataset.halts), 0)

    def test_sample_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            summary = run_sample(output, Path("config/market_data.sample.json"))
            self.assertEqual(summary["quality_errors"], 0)
            self.assertTrue((output / "features.jsonl").exists())
            self.assertTrue((output / "quality_report.json").exists())
            self.assertTrue((output / "run_manifest.json").exists())


if __name__ == "__main__":
    unittest.main()

