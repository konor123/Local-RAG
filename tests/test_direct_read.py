import os
import sys
import tempfile
import types
import unittest

import direct_read


class DirectReadTests(unittest.TestCase):
    def setUp(self):
        self.original_worker_loader = sys.modules.get("worker_loader")

    def tearDown(self):
        if self.original_worker_loader is None:
            sys.modules.pop("worker_loader", None)
        else:
            sys.modules["worker_loader"] = self.original_worker_loader

    def _install_loader(self, func):
        module = types.ModuleType("worker_loader")
        module.load_file = func
        sys.modules["worker_loader"] = module

    def test_load_file_content_returns_text_without_embeddings(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            path = tmp.name
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        self._install_loader(lambda p: [{"page_content": "직접 열람 내용", "metadata": {"source": p}}])

        result = direct_read.load_file_content(path, max_chars=100, timeout_seconds=2)

        self.assertTrue(result["success"])
        self.assertEqual(result["source_engine"], "direct_read")
        self.assertIn("직접 열람 내용", result["content"])

    def test_loader_error_is_returned_with_category_and_detail(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            path = tmp.name
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        self._install_loader(lambda p: {"__loader_error__": True, "category": "parse_error", "detail": "broken pdf"})

        result = direct_read.load_file_content(path, max_chars=100, timeout_seconds=2)

        self.assertFalse(result["success"])
        self.assertEqual(result["category"], "parse_error")
        self.assertEqual(result["detail"], "broken pdf")
        self.assertTrue(result["ocr_needed"])

    def test_empty_pdf_reports_ocr_needed_without_running_ocr(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            path = tmp.name
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        self._install_loader(lambda p: [])

        result = direct_read.load_file_content(path, max_chars=100, timeout_seconds=2)

        self.assertFalse(result["success"])
        self.assertEqual(result["category"], "no_chunks")
        self.assertTrue(result["ocr_needed"])

    def test_unsupported_extension_is_rejected(self):
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
            path = tmp.name
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))

        result = direct_read.load_file_content(path, max_chars=100, timeout_seconds=2)

        self.assertFalse(result["success"])
        self.assertEqual(result["category"], "unsupported_extension")

    def test_direct_read_candidates_respects_total_timeout_and_max_files(self):
        """direct_read_candidates() should stop iterating when total timeout is
        exceeded and should never process more than max_files items."""
        call_count = 0

        def fake_load(path, max_chars=0, timeout_seconds=None):
            nonlocal call_count
            call_count += 1
            return {
                "success": True,
                "path": path,
                "source": path,
                "content": f"content from {path}",
                "metadata": {},
                "category": "ok",
                "detail": "",
                "source_engine": "direct_read",
                "ocr_needed": False,
            }

        original_load = direct_read.load_file_content
        original_monotonic = direct_read.time.monotonic
        direct_read.load_file_content = fake_load
        try:
            candidates = [{"path": f"/fake/file_{i}.txt", "name": f"file_{i}.txt"} for i in range(20)]

            # max_files=3 → should never see more than 3 calls
            results = direct_read.direct_read_candidates(
                candidates,
                config={"max_direct_read_files": 3, "direct_read_total_timeout_seconds": 30},
            )
            self.assertLessEqual(call_count, 3)
            self.assertLessEqual(len(results), 3)
            # All returned results should have scores/paths from the first 3 candidates
            for r in results:
                self.assertIn("file_", r["name"])

            # Verify bounded iteration: simulated elapsed time stops before the
            # second load and returns a timeout diagnostic for that candidate.
            call_count = 0
            ticks = iter([0, 0, 31])
            direct_read.time.monotonic = lambda: next(ticks)
            results2 = direct_read.direct_read_candidates(
                candidates,
                config={
                    "max_direct_read_files": 10,
                    "direct_read_total_timeout_seconds": 30,
                    "direct_read_file_timeout_seconds": 1,
                },
            )
            direct_read.time.monotonic = original_monotonic
            self.assertEqual(call_count, 1)
            self.assertEqual(results2[-1]["category"], "timeout")
        finally:
            direct_read.time.monotonic = original_monotonic
            direct_read.load_file_content = original_load


if __name__ == "__main__":
    unittest.main()
