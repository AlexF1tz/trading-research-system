from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from equity_research.catalyst_intelligence.pipeline import CatalystPipeline
from equity_research.catalyst_intelligence.provider import CatalystSourceProvider
from equity_research.catalyst_intelligence.sample import run_sample
from equity_research.catalyst_intelligence.synthetic import (
    SyntheticCatalystFixtureProvider,
)


class CatalystPipelineTests(unittest.TestCase):
    def test_fixture_provider_satisfies_replaceable_protocol(self) -> None:
        self.assertIsInstance(SyntheticCatalystFixtureProvider(), CatalystSourceProvider)

    def test_pipeline_classifies_every_source_document(self) -> None:
        result = CatalystPipeline(SyntheticCatalystFixtureProvider()).run()
        self.assertEqual(result.error_count, 0)
        self.assertEqual(len(result.events), len(result.batch.documents))
        self.assertEqual(len(result.events), 14)

    def test_sample_writes_event_quality_and_manifest_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            summary = run_sample(output, Path("config/catalyst.sample.json"))
            self.assertEqual(summary["quality_errors"], 0)
            self.assertEqual(summary["unverified_events"], 1)
            self.assertTrue((output / "events.jsonl").exists())
            self.assertTrue((output / "quality_report.json").exists())
            self.assertTrue((output / "run_manifest.json").exists())


if __name__ == "__main__":
    unittest.main()

