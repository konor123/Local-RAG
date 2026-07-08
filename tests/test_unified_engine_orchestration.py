import importlib
import sys
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
        self.logged = []
        self.engine.conversation_logger.log_interaction = lambda **kwargs: self.logged.append(kwargs)

    def tearDown(self):
        self.engine._plan_query = self.original_plan
        self.engine._execute_sub_query_streaming = self.original_execute
        self.engine._synthesize_answer = self.original_synthesize
        self.engine._review_tool_results = self.original_review
        self.engine._answer_direct = self.original_answer_direct
        self.engine.conversation_logger.log_interaction = self.original_log

    def test_direct_mode_returns_answer_without_tools(self):
        called_tools = []
        self.engine._plan_query = lambda question, history=None: {"mode": "direct", "reason": "simple"}
        self.engine._answer_direct = lambda question, history=None: iter([{"type": "answer", "content": "direct", "sources": []}])
        self.engine._execute_sub_query_streaming = lambda *args, **kwargs: called_tools.append(args) or iter([])

        events = list(self.engine.get_unified_response("안녕"))

        self.assertEqual([e for e in events if e.get("type") == "answer"][0]["content"], "direct")
        self.assertEqual(called_tools, [])

    def test_hybrid_runs_only_when_llm_requests_hybrid(self):
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

        self.assertEqual(executed, [("hybrid", "유도등 카탈로그")])

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

        self.assertEqual(executed, ["첫 검색", "수정 검색"])


if __name__ == "__main__":
    unittest.main()
