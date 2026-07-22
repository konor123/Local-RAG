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
