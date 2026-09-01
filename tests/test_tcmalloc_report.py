import importlib.util
import unittest
from pathlib import Path


TOOL_DIR = Path(__file__).resolve().parents[1] / "tcmalloc_report"
SPEC = importlib.util.spec_from_file_location("tcmalloc_report_main", TOOL_DIR / "main.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
_heap_number = MODULE._heap_number
_is_tcmalloc_config = MODULE._is_tcmalloc_config
_normalise_host_path = MODULE._normalise_host_path


class TcMallocReportTests(unittest.TestCase):
    def test_tcmalloc_build_marker_is_explicit(self):
        self.assertTrue(_is_tcmalloc_config("target=mk7i\nusetcmalloc=True\n"))
        self.assertTrue(_is_tcmalloc_config("  USETCMALLOC = yes  \n"))
        self.assertFalse(_is_tcmalloc_config("target=mk7i\nuseasandefault=True\n"))

    def test_host_path_must_be_absolute_and_is_normalised(self):
        self.assertEqual(
            _normalise_host_path("/home/mk7/development/game/build/host/"),
            "/home/mk7/development/game/build/host",
        )
        with self.assertRaises(ValueError):
            _normalise_host_path("development/game/build/host")

    def test_heap_number_is_read_from_filename(self):
        self.assertEqual(_heap_number("/logs/tcmalloc.0028.heap"), "0028")
        self.assertEqual(_heap_number("/logs/not-a-heap.txt"), "")


if __name__ == "__main__":
    unittest.main()
