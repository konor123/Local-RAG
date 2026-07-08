import unittest


class EvidenceSourceTests(unittest.TestCase):
    def test_synthesize_answer_preserves_source_evidence_metadata(self):
        try:
            import unified_engine
        except ImportError as exc:
            self.skipTest(f"unified_engine dependencies unavailable: {exc}")

        class Provider:
            def synthesize(self, question, full_context, history_str):
                return "answer"

        original_get_provider = unified_engine.get_provider
        unified_engine.get_provider = lambda: Provider()
        try:
            events = list(unified_engine._synthesize_answer(
                "question",
                [{
                    "sub_query": {"query": "alarm"},
                    "data": {
                        "type": "hybrid",
                        "result": {
                            "count": 1,
                            "results": [{
                                "content": "alarm evidence snippet",
                                "source": "C:/docs/alarm.pdf",
                                "source_engine": "sqlite_fts5",
                                "score": 0.42,
                                "metadata": {"page": 7},
                            }],
                        },
                    },
                }],
                [],
            ))
        finally:
            unified_engine.get_provider = original_get_provider

        answer = [event for event in events if event.get("type") == "answer"][0]
        source = answer["sources"][0]
        self.assertEqual(source["source_engine"], "sqlite_fts5")
        self.assertEqual(source["metadata"]["page"], 7)
        self.assertEqual(source["score"], 0.42)
        self.assertIn("alarm evidence", source["snippet"])

    def test_source_card_html_renders_engine_metadata_and_snippet(self):
        try:
            import native_ui
        except ImportError as exc:
            self.skipTest(f"native_ui dependencies unavailable: {exc}")

        html = native_ui._source_card_html({
            "source": "C:/docs/alarm.pdf",
            "source_engine": "sqlite_fts5",
            "score": 0.12345,
            "metadata": {"page": 3},
            "snippet": "alarm snippet",
        })

        self.assertIn("FTS5", html)
        self.assertIn("page: 3", html)
        self.assertIn("alarm snippet", html)
        self.assertIn("background:#ffffff", html)
        self.assertNotIn("background:#0f172a", html)

    def test_source_card_html_hides_lone_filename_badge(self):
        try:
            import native_ui
        except ImportError as exc:
            self.skipTest(f"native_ui dependencies unavailable: {exc}")

        html = native_ui._source_card_html({
            "source": "C:/Users/OSLENG/Desktop/산업기능요원 병역지정업체 신청/증빙서류 사실 확인서.pdf",
            "source_engine": "filename",
        })

        self.assertIn("증빙서류 사실 확인서.pdf", html)
        self.assertIn("C:/Users/OSLENG/Desktop", html)
        self.assertNotIn(">파일명<", html)


if __name__ == "__main__":
    unittest.main()
