import unittest


class SearchResultTests(unittest.TestCase):
    def test_file_result_normalization(self):
        from search_result import from_file_result

        result = from_file_result({"name": "견적.xlsx", "path": "C:/docs/견적.xlsx", "last_modified": "2026-07-01"})

        self.assertEqual(result.display_name, "견적.xlsx")
        self.assertEqual(result.source_path, "C:/docs/견적.xlsx")
        self.assertEqual(result.source_engine, "filename")
        self.assertEqual(result.metadata["last_modified"], "2026-07-01")

    def test_content_result_normalization(self):
        from search_result import from_content_result

        result = from_content_result({
            "content": "본문 일부",
            "source": "C:/docs/manual.pdf",
            "metadata": {"page": 3},
            "score": 0.8,
        })

        self.assertEqual(result.display_name, "manual.pdf")
        self.assertEqual(result.snippet, "본문 일부")
        self.assertEqual(result.metadata["page"], 3)
        self.assertEqual(result.source_engine, "vector")


class RrfTests(unittest.TestCase):
    def test_rrf_promotes_result_seen_by_multiple_engines(self):
        from rrf import reciprocal_rank_fusion
        from search_result import SearchResult

        shared_file = SearchResult("C:/docs/shared.pdf", "shared.pdf", source_engine="filename")
        file_only = SearchResult("C:/docs/file_only.pdf", "file_only.pdf", source_engine="filename")
        shared_content = SearchResult("C:/docs/shared.pdf", "shared.pdf", source_engine="vector")

        fused = reciprocal_rank_fusion([[shared_file, file_only], [shared_content]], limit=2)

        self.assertEqual(fused[0].source_path, "C:/docs/shared.pdf")
        self.assertGreater(fused[0].score, fused[1].score)

    def test_rrf_is_deterministic_for_ties(self):
        from rrf import reciprocal_rank_fusion
        from search_result import SearchResult

        a = SearchResult("C:/b.pdf", "b.pdf")
        b = SearchResult("C:/a.pdf", "a.pdf")

        fused = reciprocal_rank_fusion([[a], [b]], limit=2)

        self.assertEqual([r.display_name for r in fused], ["a.pdf", "b.pdf"])


class HybridSearchTests(unittest.TestCase):
    def test_router_detects_mixed_case_hybrid_intent(self):
        from query_router import classify_query

        self.assertEqual(classify_query("Catalog 내용 확인해줘"), "hybrid")

    def test_hybrid_search_fuses_existing_tool_results(self):
        import tools
        from hybrid_search import hybrid_search

        original_search_files = tools.search_files
        original_search_content = tools.search_content
        original_search_metadata_content = tools.search_metadata_content
        tools.search_files = lambda pattern, sort_by="date_newest": {
            "count": 1,
            "results": [{"name": "shared.pdf", "path": "C:/docs/shared.pdf"}],
        }
        tools.search_content = lambda query, k=5: {
            "count": 1,
            "results": [{"content": "검색된 본문", "source": "C:/docs/shared.pdf", "metadata": {}, "score": 0.9}],
            "sources": ["C:/docs/shared.pdf"],
        }
        tools.search_metadata_content = lambda query, k=5: {"count": 0, "results": [], "sources": [], "disabled": True}
        try:
            result = hybrid_search("shared", k=3)
        finally:
            tools.search_files = original_search_files
            tools.search_content = original_search_content
            tools.search_metadata_content = original_search_metadata_content

        self.assertEqual(result["source"], "hybrid_rrf")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["sources"], ["C:/docs/shared.pdf"])
        self.assertEqual(result["results"][0]["source"], "C:/docs/shared.pdf")

    def test_hybrid_search_uses_tokenized_filename_patterns(self):
        import tools
        from hybrid_search import hybrid_search

        seen_patterns = []
        original_search_files = tools.search_files
        original_search_content = tools.search_content
        original_search_metadata_content = tools.search_metadata_content
        tools.search_files = lambda pattern, sort_by="date_newest": seen_patterns.append(pattern) or {"count": 0, "results": []}
        tools.search_content = lambda query, k=5: {"count": 0, "results": [], "sources": []}
        tools.search_metadata_content = lambda query, k=5: {"count": 0, "results": [], "sources": [], "disabled": True}
        try:
            hybrid_search("유도등 카탈로그 내용 요약해줘", k=3)
        finally:
            tools.search_files = original_search_files
            tools.search_content = original_search_content
            tools.search_metadata_content = original_search_metadata_content

        self.assertIn("*유도등*카탈로그*", seen_patterns)
        self.assertNotIn("*유도등 카탈로그 내용 요약해줘*", seen_patterns)

    def test_hybrid_search_includes_configured_metadata_fts_results(self):
        import tools
        from hybrid_search import hybrid_search

        original_search_files = tools.search_files
        original_search_content = tools.search_content
        original_search_metadata_content = tools.search_metadata_content
        tools.search_files = lambda pattern, sort_by="date_newest": {"count": 0, "results": []}
        tools.search_content = lambda query, k=5: {"count": 0, "results": [], "sources": []}
        tools.search_metadata_content = lambda query, k=5: {
            "count": 1,
            "results": [{
                "content": "FTS 본문",
                "source": "C:/docs/fts.pdf",
                "metadata": {"page": 2},
                "score": -0.5,
                "source_engine": "sqlite_fts5",
            }],
            "sources": ["C:/docs/fts.pdf"],
            "source": "sqlite_fts5",
        }
        try:
            result = hybrid_search("fts", k=3)
        finally:
            tools.search_files = original_search_files
            tools.search_content = original_search_content
            tools.search_metadata_content = original_search_metadata_content

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["components"]["metadata_count"], 1)
        self.assertEqual(result["results"][0]["source_engine"], "sqlite_fts5")
        self.assertEqual(result["sources"], ["C:/docs/fts.pdf"])


if __name__ == "__main__":
    unittest.main()
