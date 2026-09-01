import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ASAN_DIR = Path(__file__).resolve().parents[1] / "asan_report"
sys.path.insert(0, str(ASAN_DIR))

import main as asan_main  # noqa: E402
from offline_analyzer import analyze_file  # noqa: E402


class AsanReportIoTests(unittest.TestCase):
    def test_large_local_file_uses_bounded_preview_but_full_analysis(self):
        path = Path(__file__).parent / "fixtures" / "single_complete_asan.txt"

        with patch.object(asan_main, "MAX_REPORT_BYTES", 80):
            preview = asan_main.read_local_report(path)

        self.assertIn("Raw preview truncated", preview)
        self.assertNotIn("SUMMARY: AddressSanitizer", preview)

        result = analyze_file(path)
        self.assertEqual(result["stats"]["reports_completed"], 1)
        self.assertEqual(result["totals"]["candidate"]["bytes"], 64)
        self.assertEqual(result["reconciliation"], "matched")


if __name__ == "__main__":
    unittest.main()
