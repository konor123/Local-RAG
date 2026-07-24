import importlib
import os
import sys
import tempfile
import types
import unittest


def import_unified_engine():
    if "langchain_ollama" not in sys.modules:
        langchain_ollama = types.ModuleType("langchain_ollama")

        class ChatOllama:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def invoke(self, messages):
                return types.SimpleNamespace(content='{"action":"answer","reason":"stub"}')

        langchain_ollama.ChatOllama = ChatOllama
        sys.modules["langchain_ollama"] = langchain_ollama

    if "langchain_core.messages" not in sys.modules:
        langchain_core = types.ModuleType("langchain_core")
        messages = types.ModuleType("langchain_core.messages")

        class SystemMessage:
            def __init__(self, content=""):
                self.content = content

        class HumanMessage:
            def __init__(self, content=""):
                self.content = content

        messages.SystemMessage = SystemMessage
        messages.HumanMessage = HumanMessage
        sys.modules["langchain_core"] = langchain_core
        sys.modules["langchain_core.messages"] = messages

    rag_engine = types.ModuleType("rag_engine")
    rag_engine.LLM_MODEL = "stub-model"
    rag_engine.OLLAMA_BASE_URL = "http://localhost:11434"
    rag_engine.LLM_NUM_CTX = 4096
    rag_engine.LLM_NUM_PREDICT = 512
    rag_engine.LLM_REQUEST_TIMEOUT = 10
    sys.modules["rag_engine"] = rag_engine

    background_embedder = types.ModuleType("background_embedder")

    class BackgroundEmbedder:
        pass

    background_embedder.BackgroundEmbedder = BackgroundEmbedder
    background_embedder.EMBEDDABLE_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".hwp", ".txt"}
    sys.modules["background_embedder"] = background_embedder

    if "unified_engine" in sys.modules:
        return importlib.reload(sys.modules["unified_engine"])
    return importlib.import_module("unified_engine")


class UnifiedOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = import_unified_engine()
        self.original_plan = self.engine._plan_query
        self.original_classify_complexity = self.engine.classify_complexity
        self.original_execute = self.engine._execute_sub_query_streaming
        self.original_synthesize = self.engine._synthesize_answer
        self.original_review = self.engine._review_tool_results
        self.original_answer_direct = self.engine._answer_direct
        self.original_log = self.engine.conversation_logger.log_interaction
        self.original_search_files = self.engine.search_files
        self.original_direct_read_candidates = self.engine.direct_read_candidates
        self.original_background_embedder = self.engine.BackgroundEmbedder
        self.original_load_config = self.engine.load_config
        self.original_get_provider = self.engine.get_provider
        self.original_ocr_pdf_for_direct_read = self.engine._ocr_pdf_for_direct_read
        self.logged = []
        self.engine.conversation_logger.log_interaction = lambda **kwargs: self.logged.append(kwargs)

    def tearDown(self):
        self.engine._plan_query = self.original_plan
        self.engine.classify_complexity = self.original_classify_complexity
        self.engine._execute_sub_query_streaming = self.original_execute
        self.engine._synthesize_answer = self.original_synthesize
        self.engine._review_tool_results = self.original_review
        self.engine._answer_direct = self.original_answer_direct
        self.engine.conversation_logger.log_interaction = self.original_log
        self.engine.search_files = self.original_search_files
        self.engine.direct_read_candidates = self.original_direct_read_candidates
        self.engine.BackgroundEmbedder = self.original_background_embedder
        self.engine.load_config = self.original_load_config
        self.engine.get_provider = self.original_get_provider
        self.engine._ocr_pdf_for_direct_read = self.original_ocr_pdf_for_direct_read

    def test_direct_mode_returns_answer_without_tools(self):
        called_tools = []
        self.engine._plan_query = lambda question, history=None: {"mode": "direct", "reason": "simple"}
        self.engine._answer_direct = lambda question, history=None: iter([{"type": "answer", "content": "direct", "sources": []}])
        self.engine._execute_sub_query_streaming = lambda *args, **kwargs: called_tools.append(args) or iter([])

        events = list(self.engine.get_unified_response("안녕"))

        self.assertEqual([e for e in events if e.get("type") == "answer"][0]["content"], "direct")
        self.assertEqual(called_tools, [])

    def test_factual_fast_path_skips_planner_and_review(self):
        executed = []
        self.engine.classify_complexity = lambda question, history=None: "factual"

        def fail_if_called(*args, **kwargs):
            raise AssertionError("LLM planner or review must not run on a sufficient factual result")

        self.engine._plan_query = fail_if_called
        self.engine._review_tool_results = fail_if_called
        self.engine._synthesize_answer = lambda question, results, history: iter([
            {"type": "answer", "content": "fact", "sources": [], "source_count": 0}
        ])

        def execute(sub_query, allow_jit=True):
            executed.append((sub_query, allow_jit))
            yield (None, {
                "type": "content",
                "result": {"count": 1, "results": [{"content": "근거", "score": 0.9}]},
            })

        self.engine._execute_sub_query_streaming = execute

        events = list(self.engine.get_unified_response("OSL-FD-IR3X의 정격전압은?"))

        self.assertEqual(events[-1]["content"], "fact")
        self.assertEqual(executed, [(
            {"type": "content", "query": "OSL-FD-IR3X의 정격전압은?", "reason": "High-confidence factual fast path"},
            False,
        )])
        self.assertEqual(self.logged[0]["plan"]["complexity_tier"], "factual")
        self.assertTrue(self.logged[0]["plan"]["fast_path"])

    def test_contextual_product_question_with_history_uses_planner(self):
        plan_calls = []
        self.engine._plan_query = lambda question, history=None: plan_calls.append((question, history)) or {
            "mode": "direct",
            "reason": "contextual question",
        }
        self.engine._answer_direct = lambda question, history=None: iter([
            {"type": "answer", "content": "planned", "sources": []}
        ])
        self.engine._execute_sub_query_streaming = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("contextual question must not use factual fast path")
        )

        list(self.engine.get_unified_response(
            "그 제품의 가격은?",
            [("user", "제품 설명"), ("assistant", "제품 A입니다.")],
        ))

        self.assertEqual(plan_calls, [(
            "그 제품의 가격은?",
            [("user", "제품 설명"), ("assistant", "제품 A입니다.")],
        )])

    def test_empty_factual_result_escalates_to_existing_planner(self):
        executed = []
        self.engine.classify_complexity = lambda question, history=None: "factual"
        plan_calls = []
        self.engine._plan_query = lambda question, history=None: plan_calls.append(question) or {
            "mode": "tools",
            "sub_queries": [{"type": "content", "query": "보완 검색", "reason": "retry"}],
        }
        self.engine._review_tool_results = lambda *args, **kwargs: {"action": "answer", "reason": "enough"}
        self.engine._synthesize_answer = lambda question, results, history: iter([
            {"type": "answer", "content": "recovered", "sources": [], "source_count": 0}
        ])

        def execute(sub_query, allow_jit=True):
            executed.append(sub_query["query"])
            count = 0 if sub_query["query"] == "초기 질문" else 1
            yield (None, {"type": "content", "result": {"count": count, "results": []}})

        self.engine._execute_sub_query_streaming = execute

        events = list(self.engine.get_unified_response("초기 질문"))

        self.assertEqual(events[-1]["content"], "recovered")
        self.assertEqual(plan_calls, ["초기 질문"])
        self.assertEqual((executed[0], executed[-1]), ("초기 질문", "보완 검색"))
        self.assertFalse(self.logged[0]["plan"]["fast_path"])
        self.assertTrue(self.logged[0]["plan"]["fast_path_escalated"])

    def test_hybrid_runs_when_llm_requests_hybrid_with_file_safety_net(self):
        executed = []
        self.engine._plan_query = lambda question, history=None: {
            "mode": "tools",
            "sub_queries": [{"type": "hybrid", "query": "유도등 카탈로그", "reason": "LLM chose hybrid"}],
        }
        self.engine._review_tool_results = lambda *args, **kwargs: {"action": "answer", "reason": "enough"}
        self.engine._synthesize_answer = lambda *args, **kwargs: iter([{"type": "answer", "content": "done", "sources": []}])

        def execute(sq, allow_jit=True):
            executed.append((sq["type"], sq["query"]))
            yield (None, {"type": sq["type"], "result": {"count": 1, "results": []}})

        self.engine._execute_sub_query_streaming = execute

        list(self.engine.get_unified_response("카탈로그 내용 확인해줘"))

        self.assertEqual(executed[0][0], "file")
        self.assertEqual(executed[1], ("hybrid", "유도등 카탈로그"))

    def test_llm_order_is_preserved_and_content_not_skipped_after_file_hit(self):
        executed = []
        self.engine._plan_query = lambda question, history=None: {
            "mode": "tools",
            "sub_queries": [
                {"type": "content", "query": "본문 먼저", "reason": "semantic"},
                {"type": "file", "query": "*파일*", "reason": "then file"},
            ],
        }
        self.engine._review_tool_results = lambda *args, **kwargs: {"action": "answer", "reason": "enough"}
        self.engine._synthesize_answer = lambda *args, **kwargs: iter([{"type": "answer", "content": "done", "sources": []}])

        def execute(sq, allow_jit=True):
            executed.append(sq["type"])
            yield (None, {"type": sq["type"], "result": {"count": 5, "results": []}})

        self.engine._execute_sub_query_streaming = execute

        list(self.engine.get_unified_response("본문과 파일 둘 다"))

        self.assertEqual(executed, ["content", "file"])

    def test_jit_skips_remaining_files_after_vectorstore_failure(self):
        calls = []

        class FailingEmbedder:
            def process_single_file_synchronous(self, path):
                calls.append(path)
                return {
                    "success": False,
                    "category": "embedding_error",
                    "detail": "VectorStore load failed: corrupt index",
                }

        self.engine.BackgroundEmbedder = FailingEmbedder
        self.engine.EMBEDDABLE_EXTENSIONS = {".pdf"}
        events = []
        original_processed_path = os.environ.get("PROCESSED_FILES_PATH")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as processed_file:
            processed_path = processed_file.name
        os.environ["PROCESSED_FILES_PATH"] = processed_path
        try:
            self.engine._perform_jit_ingestion(
                [{"path": "first.pdf"}, {"path": "second.pdf"}],
                events,
            )
        finally:
            if original_processed_path is None:
                os.environ.pop("PROCESSED_FILES_PATH", None)
            else:
                os.environ["PROCESSED_FILES_PATH"] = original_processed_path
            os.remove(processed_path)

        self.assertEqual(calls, ["first.pdf"])
        self.assertTrue(any("나머지 1개 파일" in event["content"] for event in events))

    def test_tools_plan_without_file_gets_generic_file_safety_net(self):
        executed = []
        self.engine._plan_query = lambda question, history=None: {
            "mode": "tools",
            "sub_queries": [{"type": "content", "query": "중소기업확인서", "reason": "LLM chose content"}],
        }
        self.engine._review_tool_results = lambda *args, **kwargs: {"action": "answer", "reason": "enough"}
        self.engine._synthesize_answer = lambda *args, **kwargs: iter([{"type": "answer", "content": "done", "sources": []}])

        def execute(sq, allow_jit=True):
            executed.append((sq["type"], sq["query"]))
            yield (None, {"type": sq["type"], "result": {"count": 1, "results": []}})

        self.engine._execute_sub_query_streaming = execute

        list(self.engine.get_unified_response("중소기업확인서"))

        self.assertEqual(executed[0], ("file", "*중소기업확인서*"))
        self.assertEqual(executed[1], ("content", "중소기업확인서"))

    def test_review_can_request_revised_search(self):
        executed = []
        self.engine._plan_query = lambda question, history=None: {
            "mode": "tools",
            "sub_queries": [{"type": "content", "query": "첫 검색", "reason": "initial"}],
        }
        reviews = [
            {"action": "search_more", "reason": "empty", "sub_queries": [{"type": "content", "query": "수정 검색", "reason": "retry"}]},
            {"action": "answer", "reason": "enough"},
        ]
        self.engine._review_tool_results = lambda *args, **kwargs: reviews.pop(0)
        self.engine._synthesize_answer = lambda *args, **kwargs: iter([{"type": "answer", "content": "done", "sources": []}])

        def execute(sq, allow_jit=True):
            executed.append(sq["query"])
            yield (None, {"type": sq["type"], "result": {"count": 0, "results": []}})

        self.engine._execute_sub_query_streaming = execute

        list(self.engine.get_unified_response("다시 찾아줘"))

        self.assertEqual(executed[0].startswith("*"), True)
        self.assertEqual(executed[1:], ["첫 검색", "수정 검색"])

    def test_complex_multiround_passes_accumulated_evidence_ledger_to_synthesis(self):
        original_max_planning_rounds = self.engine.MAX_PLANNING_ROUNDS
        self.engine.MAX_PLANNING_ROUNDS = 3
        self.addCleanup(
            setattr,
            self.engine,
            "MAX_PLANNING_ROUNDS",
            original_max_planning_rounds,
        )
        captured = {}
        self.engine.classify_complexity = lambda question, history=None: "complex"
        self.engine._plan_query = lambda question, history=None: {
            "mode": "tools",
            "sub_queries": [
                {"type": "file", "query": "*제품*", "reason": "source lookup"},
                {"type": "content", "query": "제품 A 전압", "reason": "first fact"},
            ],
        }
        reviews = [
            {
                "action": "search_more",
                "reason": "제품 B 근거 부족",
                "sub_queries": [{"type": "content", "query": "제품 B 전압", "reason": "missing fact"}],
                "confirmed_facts": [{"fact": "제품 A의 전압은 220V", "source_path": "source.pdf", "chunk": "A-1"}],
                "missing": ["제품 B의 전압"],
                "rationale": "제품 A만 확인됨",
            },
            {
                "action": "answer",
                "reason": "충분한 근거",
                "confirmed_facts": [{"fact": "제품 B의 전압은 110V", "source_path": "source.pdf", "chunk": "B-1"}],
                "missing": [],
                "rationale": "두 제품 모두 확인됨",
            },
        ]
        self.engine._review_tool_results = lambda *args, **kwargs: reviews.pop(0)

        def synthesize(question, results, history, evidence_ledger):
            captured["ledger"] = evidence_ledger
            return iter([{"type": "answer", "content": "done", "sources": [], "source_count": 0}])

        self.engine._synthesize_answer = synthesize

        def execute(sub_query, allow_jit=True):
            yield (None, {
                "type": sub_query["type"],
                "result": {"count": 1, "results": [{"content": sub_query["query"], "source": "source.pdf", "score": 0.9}]},
            })

        self.engine._execute_sub_query_streaming = execute

        list(self.engine.get_unified_response("두 제품의 전압을 비교해줘"))

        self.assertEqual(len(captured["ledger"]), 2)
        self.assertEqual(captured["ledger"][0]["selected_sources"], ["source.pdf"])
        self.assertEqual(captured["ledger"][1]["selected_sources"], ["source.pdf"])
        self.assertEqual(captured["ledger"][0]["missing"], ["제품 B의 전압"])
        self.assertEqual(self.logged[0]["plan"]["rounds"][0]["ledger"], captured["ledger"][0])

    def test_review_facts_are_reduced_to_matching_selected_sources_only(self):
        captured = {}
        self.engine.classify_complexity = lambda question, history=None: "complex"
        self.engine._plan_query = lambda question, history=None: {
            "mode": "tools",
            "sub_queries": [{"type": "content", "query": "제품 A", "reason": "lookup"}],
        }
        reviews = [
            {
                "action": "search_more",
                "sub_queries": [{"type": "content", "query": "재검색", "reason": "verify"}],
                "confirmed_facts": [
                    {"fact": "LLM이 지어낸 사실", "source_path": "not-in-results.pdf", "chunk": "fake"},
                    {"fact": "검색 결과에 없는 청크", "source_path": "source.pdf", "chunk": "not-a-real-chunk"},
                ],
                "missing": [],
                "rationale": "LLM 자유형 추론은 ledger에 보관하지 않음",
            },
            {"action": "answer"},
        ]
        self.engine._review_tool_results = lambda *args, **kwargs: reviews.pop(0)

        def synthesize(question, results, history, evidence_ledger):
            captured["ledger"] = evidence_ledger
            return iter([{"type": "answer", "content": "done", "sources": []}])

        self.engine._synthesize_answer = synthesize
        self.engine._execute_sub_query_streaming = lambda sub_query, allow_jit=True: iter([
            (None, {
                "type": "content",
                "result": {"count": 1, "results": [{"content": "실제 결과", "source": "source.pdf"}]},
            })
        ])

        list(self.engine.get_unified_response("제품 A의 사실을 확인해줘"))

        self.assertEqual(captured["ledger"][0]["selected_sources"], ["source.pdf"])
        self.assertNotIn("confirmed_facts", captured["ledger"][0])
        self.assertNotIn("LLM이 지어낸 사실", repr(captured["ledger"]))
        self.assertNotIn("not-a-real-chunk", repr(captured["ledger"]))

    def test_first_review_supports_legacy_four_argument_mock_without_ledger_keyword(self):
        review_calls = []
        self.engine._plan_query = lambda question, history=None: {
            "mode": "tools",
            "sub_queries": [{"type": "content", "query": "초기 검색", "reason": "lookup"}],
        }

        def legacy_review(question, results, history, round_index):
            review_calls.append((question, results, history, round_index))
            return {"action": "answer", "reason": "enough"}

        self.engine._review_tool_results = legacy_review
        self.engine._synthesize_answer = lambda question, results, history: iter([
            {"type": "answer", "content": "done", "sources": []}
        ])
        self.engine._execute_sub_query_streaming = lambda sub_query, allow_jit=True: iter([
            (None, {"type": "content", "result": {"count": 1, "results": []}})
        ])

        list(self.engine.get_unified_response("초기 검색 결과를 알려줘"))

        self.assertEqual(len(review_calls), 1)
        self.assertEqual(review_calls[0][0], "초기 검색 결과를 알려줘")
        self.assertEqual(review_calls[0][3], 1)

    def test_max_round_new_result_clears_stale_missing_and_rationale_before_synthesis(self):
        original_max_planning_rounds = self.engine.MAX_PLANNING_ROUNDS
        self.engine.MAX_PLANNING_ROUNDS = 2
        self.addCleanup(setattr, self.engine, "MAX_PLANNING_ROUNDS", original_max_planning_rounds)
        captured = {}
        self.engine.classify_complexity = lambda question, history=None: "complex"
        self.engine._plan_query = lambda question, history=None: {
            "mode": "tools",
            "sub_queries": [{"type": "content", "query": "첫 검색", "reason": "initial"}],
        }
        self.engine._review_tool_results = lambda *args, **kwargs: {
            "action": "search_more",
            "sub_queries": [{"type": "content", "query": "마지막 검색", "reason": "retry"}],
            "confirmed_facts": [{"fact": "최종 결과의 사실", "source_path": "source.pdf"}],
            "missing": ["이전에는 없던 정보"],
            "rationale": "이전 검색 결과가 부족함",
        }

        def synthesize(question, results, history, evidence_ledger):
            captured["ledger"] = evidence_ledger
            return iter([{"type": "answer", "content": "done", "sources": []}])

        self.engine._synthesize_answer = synthesize
        calls = []

        def execute(sub_query, allow_jit=True):
            calls.append(sub_query["query"])
            source = "old.pdf" if len(calls) == 1 else "source.pdf"
            yield (None, {
                "type": "content",
                "result": {"count": 1, "results": [{"content": "실제 검색 결과", "source": source}]},
            })

        self.engine._execute_sub_query_streaming = execute

        list(self.engine.get_unified_response("최종 근거를 확인해줘"))

        self.assertIn("첫 검색", calls)
        self.assertIn("마지막 검색", calls)
        self.assertLess(calls.index("첫 검색"), calls.index("마지막 검색"))
        self.assertTrue(captured["ledger"])
        self.assertTrue(all(not entry["missing"] and not entry["rationale"] for entry in captured["ledger"]))

    def test_single_round_complex_query_keeps_legacy_synthesis_signature(self):
        self.engine.classify_complexity = lambda question, history=None: "complex"
        self.engine._plan_query = lambda question, history=None: {
            "mode": "tools",
            "sub_queries": [{"type": "file", "query": "*제품*", "reason": "source lookup"}],
        }
        self.engine._review_tool_results = lambda *args, **kwargs: {
            "action": "answer",
            "reason": "enough",
            "confirmed_facts": [{"fact": "제품 A의 전압은 220V", "source_path": "A.pdf", "chunk": "A-1"}],
        }
        self.engine._synthesize_answer = lambda question, results, history: iter([
            {"type": "answer", "content": "done", "sources": [], "source_count": 0}
        ])
        self.engine._execute_sub_query_streaming = lambda sub_query, allow_jit=True: iter([
            (None, {"type": sub_query["type"], "result": {"count": 1, "results": []}})
        ])

        list(self.engine.get_unified_response("제품 A 전압은?"))

        self.assertNotIn("ledger", self.logged[0]["plan"]["rounds"][0])

    def test_synthesis_prepends_evidence_ledger_without_changing_sources(self):
        captured = {}

        class Provider:
            def synthesize(self, question, context, history):
                captured["context"] = context
                return "ledger answer"

        self.engine.get_provider = lambda: Provider()
        execution_results = [{
            "sub_query": {"type": "content", "query": "제품 A 전압"},
            "data": {
                "type": "content",
                "result": {
                    "count": 1,
                    "results": [{
                        "content": "제품 A 정격전압은 220V",
                        "source": "A.pdf",
                        "score": 0.9,
                        "metadata": {"page": 1},
                    }],
                },
            },
        }]
        evidence_ledger = [{
            "round": 1,
            "selected_sources": ["A.pdf"],
            "missing": ["제품 B의 전압"],
            "rationale": "제품 A만 확인됨",
        }]

        events = list(self.engine._synthesize_answer(
            "두 제품의 전압을 비교해줘",
            execution_results,
            [],
            evidence_ledger=evidence_ledger,
        ))

        self.assertTrue(captured["context"].startswith("=== 검토된 근거 상태"))
        self.assertIn("검토에서 선택된 근거 문서: A.pdf", captured["context"])
        self.assertIn("제품 A 정격전압은 220V", captured["context"])
        self.assertEqual(events[0]["sources"][0]["source"], "A.pdf")
        self.assertEqual(events[0]["source_count"], 1)

    def test_jit_failure_emits_error_with_category_and_detail(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        self.addCleanup(lambda: os.path.exists(tmp_path) and os.remove(tmp_path))
        processed_fd, processed_path = tempfile.mkstemp()
        os.close(processed_fd)
        self.addCleanup(lambda: os.path.exists(processed_path) and os.remove(processed_path))
        old_env = os.environ.get("PROCESSED_FILES_PATH")
        os.environ["PROCESSED_FILES_PATH"] = processed_path
        self.addCleanup(lambda: os.environ.pop("PROCESSED_FILES_PATH", None) if old_env is None else os.environ.__setitem__("PROCESSED_FILES_PATH", old_env))

        class FailingEmbedder:
            def process_single_file_synchronous(self, path):
                return {"success": False, "category": "no_chunks", "detail": "Loader returned no chunks", "path": path}

        self.engine.BackgroundEmbedder = FailingEmbedder
        events = []

        self.engine._perform_jit_ingestion([{"path": tmp_path, "name": os.path.basename(tmp_path)}], events)

        error_events = [e for e in events if e.get("type") == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertIn("no_chunks", error_events[0]["content"])
        self.assertIn("Loader returned no chunks", error_events[0]["content"])

    def test_file_query_attaches_direct_read_results(self):
        self.engine.load_config = lambda: {"search": {"enable_query_time_direct_read_fallback": True, "max_jit_files": 0}}
        self.engine.search_files = lambda query, sort_by="date_newest": {
            "query": query,
            "count": 1,
            "results": [{"path": "C:/docs/cert.pdf", "name": "cert.pdf"}],
        }
        self.engine.direct_read_candidates = lambda results, config=None: [{
            "success": True,
            "path": "C:/docs/cert.pdf",
            "name": "cert.pdf",
            "content": "주민등록번호 900101-1234567 포함",
            "source_engine": "direct_read",
            "category": "ok",
        }]

        outputs = list(self.engine._execute_sub_query_streaming({"type": "file", "query": "*cert*"}, allow_jit=False))
        data = outputs[-1][1]

        self.assertEqual(data["result"]["results"][0]["direct_read"]["source_engine"], "direct_read")
        self.assertIn("900101", data["result"]["results"][0]["direct_read"]["content"])

    def test_file_query_auto_ocrs_when_direct_read_needs_ocr(self):
        self.engine.load_config = lambda: {
            "search": {
                "enable_query_time_direct_read_fallback": True,
                "max_jit_files": 0,
                "ocr": {"enabled": True, "auto_on_direct_read": True, "direct_read_ocr_timeout_sec": 5, "direct_read_ocr_max_pages": 2},
            }
        }
        self.engine.search_files = lambda query, sort_by="date_newest": {
            "query": query,
            "count": 1,
            "results": [{"path": "C:/docs/scan.pdf", "name": "scan.pdf"}],
        }
        self.engine.direct_read_candidates = lambda results, config=None: [{
            "success": False,
            "path": "C:/docs/scan.pdf",
            "name": "scan.pdf",
            "content": "",
            "source_engine": "direct_read",
            "category": "no_chunks",
            "detail": "Loader returned no text",
            "ocr_needed": True,
        }]
        calls = []
        self.engine._ocr_pdf_for_direct_read = lambda path, cfg: calls.append((path, cfg)) or {
            "success": True,
            "content": "OCR로 읽은 주민등록번호 900101-1234567",
            "errors": [],
        }

        outputs = list(self.engine._execute_sub_query_streaming({"type": "file", "query": "*scan*"}, allow_jit=False))
        data = outputs[-1][1]
        direct = data["result"]["results"][0]["direct_read"]
        thinking = [event[0]["content"] for event in outputs if event[0] and event[0].get("type") == "thinking"]

        self.assertEqual(calls[0][0], "C:/docs/scan.pdf")
        self.assertTrue(direct["success"])
        self.assertTrue(direct["ocr_applied"])
        self.assertEqual(direct["source_engine"], "ocr_direct_read")
        self.assertIn("OCR로 읽은", direct["content"])
        self.assertTrue(any("OCR 엔진으로 읽는 중" in msg for msg in thinking))

    def test_synthesis_includes_direct_read_content_and_source_engine(self):
        captured = {}

        class Provider:
            def synthesize(self, question, context, history):
                captured["context"] = context
                return "직접 열람 답변"

        self.engine.get_provider = lambda: Provider()
        execution_results = [{
            "sub_query": {"type": "file", "query": "*cert*"},
            "data": {
                "type": "file",
                "result": {
                    "count": 1,
                    "results": [{
                        "path": "C:/docs/cert.pdf",
                        "name": "cert.pdf",
                        "direct_read": {
                            "success": True,
                            "content": "주민등록번호 900101-1234567 포함",
                            "source_engine": "direct_read",
                        },
                    }],
                },
            },
        }]

        events = list(self.engine._synthesize_answer("주민등록번호 있나?", execution_results, []))
        answer = [e for e in events if e.get("type") == "answer"][0]

        self.assertIn("주민등록번호 900101-1234567 포함", captured["context"])
        self.assertEqual(answer["sources"][0]["source_engine"], "direct_read")
        self.assertIn("900101", answer["sources"][0]["snippet"])


if __name__ == "__main__":
    unittest.main()
