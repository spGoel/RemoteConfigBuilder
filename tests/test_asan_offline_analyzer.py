import sys
import unittest
from pathlib import Path


ASAN_DIR = Path(__file__).resolve().parents[1] / "asan_report"
sys.path.insert(0, str(ASAN_DIR))

from offline_analyzer import analyze_text, format_analysis  # noqa: E402


class OfflineAsanAnalyzerTests(unittest.TestCase):
    def test_candidates_and_catalog_suppressions_are_separated(self):
        report = """==1==ERROR: LeakSanitizer: detected memory leaks

Direct leak of 64 byte(s) in 2 object(s) allocated from:
    #0 0x1000 in malloc asan_malloc_linux.cpp:69
    #1 0x1001 in CreateWidget project/widget.cpp:42

Direct leak of 32 byte(s) in 1 object(s) allocated from:
    #0 0x2000 in calloc asan_malloc_linux.cpp:77
    #1 0x2001 in gdk_pid_init GDK_PIDObjects.cpp:247

SUMMARY: AddressSanitizer: 96 byte(s) leaked in 3 allocation(s).
"""
        result = analyze_text(report)

        self.assertEqual(len(result["candidate"]), 1)
        self.assertEqual(result["candidate"][0]["bytes"], 64)
        self.assertEqual(len(result["suppressed"]), 1)
        self.assertEqual(result["suppressed"][0]["rule_id"], "TXL-2716")
        self.assertEqual(result["reconciliation"], "matched")
        self.assertIn("- TXL-2716: 1 pattern", format_analysis(result))

    def test_conditional_and_indirect_findings_need_review(self):
        report = """==2==ERROR: LeakSanitizer: detected memory leaks

Direct leak of 24 byte(s) in 1 object(s) allocated from:
    #0 0x3000 in malloc asan_malloc_linux.cpp:69
    #1 0x3001 in usbdisp_init display.cpp:10
    #2 0x3002 in _createDC display.cpp:20

Indirect leak of 40 byte(s) in 1 object(s) allocated from:
    #0 0x4000 in malloc asan_malloc_linux.cpp:69
    #1 0x4001 in CreateChild project/child.cpp:8

SUMMARY: AddressSanitizer: 64 byte(s) leaked in 2 allocation(s).
"""
        result = analyze_text(report)

        self.assertFalse(result["candidate"])
        self.assertEqual(
            {entry["rule_id"] for entry in result["uncertain"]},
            {"TXL-7079", "INDIRECT-OWNER"},
        )

    def test_libvorbisfile_is_suppressed_only_for_external_only_stacks(self):
        report = """==7==ERROR: LeakSanitizer: detected memory leaks

Direct leak of 56 byte(s) in 1 object(s) allocated from:
    #0 0x1000 in calloc asan_malloc_linux.cpp:70
    #1 0x1001 in ov_open_callbacks (/usr/lib/libvorbisfile.so.3+0x4b54)

Direct leak of 24 byte(s) in 1 object(s) allocated from:
    #0 0x2000 in calloc asan_malloc_linux.cpp:70
    #1 0x2001 in ov_open_callbacks (/usr/lib/libvorbisfile.so.3+0x4bc0)
    #2 0x2002 in ProjectAudioOwner project/audio.cpp:42

SUMMARY: AddressSanitizer: 80 byte(s) leaked in 2 allocation(s).
"""
        result = analyze_text(report)

        self.assertEqual(len(result["suppressed"]), 1)
        self.assertEqual(
            result["suppressed"][0]["rule_id"], "CONFIRMED-2026-09-01-1"
        )
        self.assertEqual(len(result["candidate"]), 1)
        self.assertIn("ProjectAudioOwner", " ".join(result["candidate"][0]["frames"]))

    def test_incomplete_trailing_report_is_dropped(self):
        report = """==3==ERROR: LeakSanitizer: detected memory leaks

Direct leak of 128 byte(s) in 1 object(s) allocated from:
    #0 0x5000 in malloc asan_malloc_linux.cpp:69
    #1 0x5001 in InterruptedAllocation project/incomplete.cpp:9
"""
        result = analyze_text(report)

        self.assertFalse(result["candidate"])
        self.assertEqual(result["stats"]["reports_dropped_incomplete"], 1)
        self.assertEqual(result["stats"]["dropped_bytes"], 128)
        self.assertEqual(result["stats"]["dropped_objects"], 1)

    def test_repeated_reports_are_aggregated_by_stack(self):
        single = """=={pid}==ERROR: LeakSanitizer: detected memory leaks

Direct leak of 16 byte(s) in 1 object(s) allocated from:
    #0 0x{address} in malloc asan_malloc_linux.cpp:69
    #1 0x{site} in RepeatedAllocation project/repeat.cpp:12

SUMMARY: AddressSanitizer: 16 byte(s) leaked in 1 allocation(s).
"""
        report = single.format(pid=4, address="6000", site="6001")
        report += single.format(pid=5, address="7000", site="7001")
        result = analyze_text(report)

        self.assertEqual(len(result["candidate"]), 1)
        finding = result["candidate"][0]
        self.assertEqual(finding["bytes"], 32)
        self.assertEqual(finding["objects"], 2)
        self.assertEqual(finding["occurrences"], 2)
        self.assertEqual(result["stats"]["reports_completed"], 2)

    def test_formatted_output_warns_that_candidates_are_not_validated(self):
        report = """Direct leak of 8 byte(s) in 1 object(s) allocated from:
    #0 0x8000 in malloc asan_malloc_linux.cpp:69
    #1 0x8001 in SmallLeak project/small.cpp:3

SUMMARY: AddressSanitizer: 8 byte(s) leaked in 1 allocation(s).
"""
        output = format_analysis(analyze_text(report))

        self.assertIn("Candidate does not mean validated", output)
        self.assertIn("RESULT: 1 possible leak pattern needs review", output)
        self.assertIn("POSSIBLE LEAKS - INVESTIGATE THESE", output)
        self.assertIn("1. Direct leak - 8 B in 1 object", output)
        self.assertIn("Allocation site: in SmallLeak", output)


if __name__ == "__main__":
    unittest.main()
