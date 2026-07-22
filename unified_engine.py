# -*- coding: utf-8 -*-
"""
Unified Engine (Azure Agentic Retrieval Architecture)
Microft Azure AI Search의 'Agentic Retrieval' 디자인 패턴을 적용.
Flow: Query -> Planner (LLM) -> Parallel Execution -> Synthesis -> Answer
"""
import json
import os
import queue
import threading
from typing import Dict, Generator, List, Any
from concurrent.futures import ThreadPoolExecutor

try:
    from langchain_ollama import ChatOllama
except ImportError:  # Backward compatibility until requirements are installed.
    from langchain_community.chat_models import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage

from rag_engine import LLM_MODEL, OLLAMA_BASE_URL, LLM_NUM_CTX, LLM_NUM_PREDICT, LLM_REQUEST_TIMEOUT
from tools import search_files, search_content, search_hybrid
from background_embedder import BackgroundEmbedder, EMBEDDABLE_EXTENSIONS
from direct_read import direct_read_candidates
from conversation_logger import conversation_logger
from ai_providers.provider_manager import get_provider
from config_manager import load_config
from search_terms import build_glob_patterns, extract_search_tokens

# --- Configuration ---
MAX_WORKERS = 3  # 병렬 실행 스레드 수
CONTENT_SEARCH_TIMEOUT = 60  # content search 최대 대기 시간(초)


def _search_config() -> Dict:
    return load_config().get("search", {})


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _ocr_config() -> Dict:
    return _search_config().get("ocr", {})


def _ocr_pdf_for_direct_read(path: str, config: Dict) -> Dict:
    timeout = _positive_int(config.get("direct_read_ocr_timeout_sec"), 45)
    max_pages = _positive_int(config.get("direct_read_ocr_max_pages"), 20)
    result_queue: "queue.Queue[Dict]" = queue.Queue(maxsize=1)

    def _run() -> None:
        try:
            from ocr_utils import ocr_pdf_pages

            page_numbers = range(1, max_pages + 1)
            pages = ocr_pdf_pages(path, page_numbers=page_numbers)
            text = "\n\n".join(page.text for page in pages if getattr(page, "text", ""))
            errors = [page.error for page in pages if getattr(page, "error", None)]
            result_queue.put({"success": bool(text.strip()), "content": text.strip(), "errors": errors}, block=False)
        except Exception as exc:
            try:
                result_queue.put({"success": False, "content": "", "errors": [str(exc)]}, block=False)
            except Exception:
                pass

    # NOTE: A timeout here can orphan this daemon thread, but query
    # processing is bounded and sequential so no resource leak occurs.
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        return result_queue.get(timeout=timeout)
    except queue.Empty:
        return {"success": False, "content": "", "errors": [f"OCR timeout after {timeout}s"]}


def _significant_tokens(text: str) -> List[str]:
    return extract_search_tokens(text, limit=5)


def _fallback_file_patterns(question: str) -> List[str]:
    return build_glob_patterns(question, max_patterns=4)



MAX_PLANNING_ROUNDS = 2
MAX_SUB_QUERIES_PER_ROUND = 4
MAX_TOTAL_TOOL_CALLS = 8
ALLOWED_TOOL_TYPES = {"file", "content", "hybrid"}


def _normalize_llm_plan(plan: Dict) -> Dict:
    """Normalize legacy and LLM-led planner outputs into a stable shape."""
    if not isinstance(plan, dict):
        return {}
    mode = plan.get("mode")
    if mode == "direct":
        return {"mode": "direct", "reason": plan.get("reason", "LLM direct response")}
    sub_queries = _coerce_sub_queries(plan.get("sub_queries", []))
    if sub_queries:
        return {"mode": "tools", "sub_queries": sub_queries, "reason": plan.get("reason", "LLM tool plan")}
    return {}


def _coerce_sub_queries(raw_queries: Any, limit: int = MAX_SUB_QUERIES_PER_ROUND) -> List[Dict]:
    """Validate and trim LLM-requested tool calls while preserving LLM order."""
    if not isinstance(raw_queries, list):
        return []
    queries: List[Dict] = []
    for item in raw_queries:
        if not isinstance(item, dict):
            continue
        q_type = str(item.get("type") or item.get("tool") or "").strip().lower()
        query = str(item.get("query") or item.get("pattern") or "").strip()
        if q_type not in ALLOWED_TOOL_TYPES or not query:
            continue
        queries.append({
            "type": q_type,
            "query": query,
            "reason": str(item.get("reason") or "LLM requested tool"),
        })
        if len(queries) >= limit:
            break
    return queries


def _fallback_llm_plan(question: str) -> Dict:
    return {
        "mode": "tools",
        "fallback": True,
        "sub_queries": [
            {"type": "content", "query": question, "reason": "Planner failure fallback content search"},
            *[{"type": "file", "query": p, "reason": "Planner failure fallback file search"} for p in _fallback_file_patterns(question)[:2]],
        ],
    }


def _ensure_file_search_safety_net(plan: Dict, question: str, max_patterns: int = 2) -> Dict:
    """Add a generic filename-search safety net when the LLM omitted file search.

    This keeps the LLM's plan/order intact as much as possible while restoring the
    pre-v1.4.1 guarantee that document-like text can still be found by filename.
    It uses the shared token extractor only; no document-type suffix list.
    """
    if not isinstance(plan, dict) or plan.get("mode") != "tools":
        return plan
    sub_queries = _coerce_sub_queries(plan.get("sub_queries", []))
    if not sub_queries or any(sq.get("type") == "file" for sq in sub_queries):
        plan["sub_queries"] = sub_queries
        return plan

    patterns = _fallback_file_patterns(question)[:max_patterns]
    if not patterns:
        plan["sub_queries"] = sub_queries
        return plan

    safety_queries = [
        {"type": "file", "query": pattern, "reason": "Generic filename safety net"}
        for pattern in patterns
    ]
    plan = dict(plan)
    plan["sub_queries"] = safety_queries + sub_queries
    plan["file_safety_net"] = True
    return plan


def _format_plan_desc(sub_queries: List[Dict]) -> str:
    return "\n".join(
        f"- [{sq.get('type', '').upper()}] {sq.get('query', '')} ({sq.get('reason', '')})"
        for sq in sub_queries
    )


def _summarize_execution_results(execution_results: List[Dict], max_chars: int = 5000) -> str:
    summary = []
    for item in execution_results[-8:]:
        sq = item.get("sub_query", {})
        data = item.get("data", {})
        result = data.get("result", {}) if isinstance(data, dict) else {}
        rows = []
        for r in result.get("results", [])[:3]:
            rows.append({
                "name": r.get("name") or os.path.basename(r.get("source", "") or r.get("path", "")),
                "path": r.get("path") or r.get("source"),
                "score": r.get("score"),
                "snippet": str(r.get("content") or "")[:180],
            })
        summary.append({
            "type": data.get("type"),
            "query": sq.get("query"),
            "count": result.get("count", 0),
            "top_results": rows,
        })
    return json.dumps(summary, ensure_ascii=False, default=str)[:max_chars]


def _answer_direct(question: str, chat_history: List[tuple] = None) -> Generator[Dict, None, None]:
    history_str = ""
    if chat_history:
        for role, msg in chat_history[-5:]:
            history_str += f"{role}: {msg}\n"
    context = "검색 도구를 사용하지 않고 답변해도 되는 일반 대화/질문입니다. 내부 문서 근거가 필요한 내용이면 근거가 없다고 밝히세요."
    answer = get_provider().synthesize(question, context, history_str)
    if answer:
        yield {"type": "answer", "content": answer, "sources": [], "source_count": 0}
        return
    yield {"type": "error", "content": "Direct answer synthesis failed"}


def _review_tool_results(
    question: str,
    execution_results: List[Dict],
    chat_history: List[tuple] = None,
    round_index: int = 1,
) -> Dict:
    """Ask the LLM whether to answer, search more, or give up after a tool round."""
    history_str = ""
    if chat_history:
        for role, msg in chat_history[-3:]:
            history_str += f"{role}: {msg}\n"

    llm_kwargs = {
        "model": LLM_MODEL,
        "base_url": OLLAMA_BASE_URL,
        "temperature": 0,
        "keep_alive": -1,
        "format": "json",
        "num_ctx": LLM_NUM_CTX,
        "num_predict": LLM_NUM_PREDICT,
    }
    if ChatOllama.__module__.startswith("langchain_ollama"):
        llm_kwargs["sync_client_kwargs"] = {"timeout": LLM_REQUEST_TIMEOUT}
    else:
        llm_kwargs["timeout"] = LLM_REQUEST_TIMEOUT
    llm = ChatOllama(**llm_kwargs)
    system_prompt = """당신은 검색 전략 검토자입니다. 검색 결과를 보고 다음 행동을 JSON으로만 결정하세요.
허용 action: "answer", "search_more", "give_up".
search_more를 선택할 때만 sub_queries를 포함하세요. type은 file, content, hybrid 중 하나입니다.
이미 충분한 근거가 있으면 answer를 선택하세요. 같은 검색을 반복하지 마세요."""
    prompt = f"""대화 기록:
{history_str}

사용자 질문: {question}
현재 라운드: {round_index}
검색 결과 요약 JSON:
{_summarize_execution_results(execution_results)}

출력 예:
{{"action":"answer","reason":"충분한 근거 확보"}}
{{"action":"search_more","reason":"검색 결과 부족","sub_queries":[{{"type":"content","query":"수정 검색어","reason":"다른 표현으로 재검색"}}]}}
{{"action":"give_up","reason":"관련 근거 없음"}}
"""
    try:
        response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=prompt)])
        return json.loads(response.content)
    except Exception as exc:
        print(f"[Review] LLM review failed: {exc}")
        return {"action": "answer", "reason": "review fallback"}

def get_unified_response(
    question: str,
    chat_history: List[tuple] = None,
    stream: bool = True
) -> Generator[Dict, None, None]:
    """
    통합 응답 생성기. LLM이 검색/즉답/재검색 전략을 지휘합니다.
    """
    yield {"type": "thinking", "content": "🤔 LLM이 응답/검색 전략을 수립 중..."}

    raw_plan = _plan_query(question, chat_history)
    plan = _normalize_llm_plan(raw_plan)
    if not plan:
        yield {"type": "thinking", "content": "⚠️ LLM 계획 수립 실패, 제한적 fallback 검색으로 전환합니다."}
        plan = _fallback_llm_plan(question)
    else:
        plan = _ensure_file_search_safety_net(plan, question)

    plan_trace = {"mode": plan.get("mode", "tools"), "rounds": [], "initial_plan": plan}
    execution_results: List[Dict] = []
    final_answer = ""

    if plan.get("mode") == "direct":
        yield {"type": "thinking", "content": f"💬 LLM 판단: 검색 없이 답변합니다. ({plan.get('reason', '')})"}
        for event in _answer_direct(question, chat_history):
            if event.get("type") == "answer":
                final_answer = event.get("content", "")
            yield event
        is_success = bool(final_answer) and "죄송합니다" not in final_answer and "정보를 찾을 수 없습니다" not in final_answer
        conversation_logger.log_interaction(question=question, plan=plan_trace, answer=final_answer, success=is_success)
        return

    current_queries = _coerce_sub_queries(plan.get("sub_queries", []))
    seen_calls = set()
    total_tool_calls = 0

    def run_query(sq: Dict, allow_jit: bool = True) -> Dict:
        final_data = {}
        for event, data in _execute_sub_query_streaming(sq, allow_jit=allow_jit):
            if event is not None:
                yield event
            if data is not None:
                final_data = data
        return final_data

    for round_index in range(1, MAX_PLANNING_ROUNDS + 1):
        if not current_queries:
            break
        yield {"type": "thinking", "content": f"📋 LLM 검색 계획 Round {round_index}:\n{_format_plan_desc(current_queries)}"}
        round_record = {"round": round_index, "sub_queries": list(current_queries), "results": []}

        for sq in current_queries:
            call_key = (sq.get("type"), sq.get("query"))
            if call_key in seen_calls:
                yield {"type": "thinking", "content": f"↩️ 중복 검색 생략: [{sq.get('type')}] {sq.get('query')}"}
                continue
            if total_tool_calls >= MAX_TOTAL_TOOL_CALLS:
                yield {"type": "thinking", "content": "⛔ 최대 도구 호출 수에 도달하여 추가 검색을 중단합니다."}
                current_queries = []
                break
            seen_calls.add(call_key)
            total_tool_calls += 1
            data = yield from run_query(sq, allow_jit=sq.get("type") == "file")
            result_item = {"sub_query": sq, "data": data}
            execution_results.append(result_item)
            round_record["results"].append({
                "type": data.get("type") if isinstance(data, dict) else None,
                "count": data.get("result", {}).get("count", 0) if isinstance(data, dict) else 0,
            })

        plan_trace["rounds"].append(round_record)
        if round_index >= MAX_PLANNING_ROUNDS:
            break

        yield {"type": "thinking", "content": "🧭 LLM이 검색 결과를 검토하고 다음 전략을 결정 중..."}
        review = _review_tool_results(question, execution_results, chat_history, round_index=round_index)
        round_record["review"] = review
        action = str(review.get("action") or "answer").strip().lower()
        if action == "search_more":
            next_queries = _coerce_sub_queries(review.get("sub_queries", []))
            if next_queries:
                current_queries = next_queries
                continue
        if action == "give_up":
            final_answer = review.get("answer") or f"관련 근거를 충분히 찾지 못했습니다. ({review.get('reason', '검색 결과 부족')})"
            yield {"type": "answer", "content": final_answer, "sources": [], "source_count": 0}
            break
        break

    if not final_answer:
        yield {"type": "thinking", "content": "🧠 LLM이 수집된 근거를 종합하여 답변을 생성합니다..."}
        for event in _synthesize_answer(question, execution_results, chat_history):
            if event.get('type') == 'answer':
                final_answer = event.get('content', '')
            yield event

    is_success = bool(final_answer) and "죄송합니다" not in final_answer and "정보를 찾을 수 없습니다" not in final_answer
    conversation_logger.log_interaction(
        question=question,
        plan=plan_trace,
        answer=final_answer,
        success=is_success
    )


def _plan_query(question: str, chat_history: List[tuple] = None) -> Dict:
    """
    LLM을 사용하여 질문을 하위 쿼리로 분해합니다. (문맥 인식)
    Gemini 3.0 Flash → Ollama 폴백
    """
    # 대화 기록 포맷팅
    history_str = ""
    if chat_history:
        for role, msg in chat_history[-3:]:
             history_str += f"{role}: {msg}\n"
    
    # 1차: Local Ollama provider (get_provider)
    result = _normalize_llm_plan(get_provider().plan_query(question, history_str))
    if result:
        print(f"[Plan] Local provider success: mode={result.get('mode')}, sub_queries={len(result.get('sub_queries', []))}")
        return result

    # 2차: deterministic fallback using the same local model with a simpler prompt
    print("[Plan] Local provider planning failed, falling back to simple local planner...")
    llm_kwargs = {
        "model": LLM_MODEL,
        "base_url": OLLAMA_BASE_URL,
        "temperature": 0,
        "keep_alive": -1,
        "format": "json",
        "num_ctx": LLM_NUM_CTX,
        "num_predict": LLM_NUM_PREDICT,
    }
    if ChatOllama.__module__.startswith("langchain_ollama"):
        llm_kwargs["sync_client_kwargs"] = {"timeout": LLM_REQUEST_TIMEOUT}
    else:
        llm_kwargs["timeout"] = LLM_REQUEST_TIMEOUT
    llm = ChatOllama(**llm_kwargs)
    
    system_prompt = f"""You are a retrieval strategy planner. Decide whether the user's request needs tools or can be answered directly.
You have access to the conversation history to resolve coreferences (e.g., 'it', 'that file', 'previous one').

Conversation History:
{history_str}

Output format: JSON only.
Allowed structures:
{{"mode":"direct","reason":"why no search is needed"}}

or

{{
  "mode": "tools",
  "sub_queries": [
    {{"type": "file", "query": "filename_pattern", "reason": "why"}},
    {{"type": "content", "query": "search_keywords", "reason": "why"}},
    {{"type": "hybrid", "query": "search_keywords", "reason": "why"}}
  ]
}}

Rules:
1. You control the strategy. Do not default to file-first; choose the tool type and order that best fits the question.
2. If the user refers to previous context (e.g. "What is the price of *that*?"), replace it with the actual subject from history.
3. Use mode="direct" only for greetings, app usage help, or general questions that do not need internal document evidence.
4. "file" type: use for locating known filenames/paths/extensions. Query should be a glob pattern (e.g. *keyword*.xlsx, *catalog*).
5. "content" type: use when document meaning/details may require embedded text search.
6. "hybrid" type: use when filename and semantic content should both be searched. Hybrid is a tool, not a routing mode.
7. Maximum 4 sub_queries. Preserve the order you want tools executed.
"""
    
    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=question)
        ])
        return _normalize_llm_plan(json.loads(response.content))
    except Exception as e:
        print(f"[Plan] Ollama fallback also failed: {e}")
        return None

def _search_content_with_timeout(query: str, k: int = 5, timeout: int = CONTENT_SEARCH_TIMEOUT) -> Dict:
    """search_content를 별도 스레드에서 실행하고 timeout 초과 시 빈 결과를 반환."""
    result_box: Dict = {}
    
    def _run():
        try:
            result_box["result"] = search_content(query, k=k)
        except Exception as e:
            result_box["error"] = str(e)
    
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    
    if t.is_alive():
        print(f"[Content] search_content timed out after {timeout}s for query: {query}")
        return {"error": f"검색 시간 초과({timeout}초)", "query": query, "count": 0, "results": [], "sources": []}
    if "error" in result_box:
        return {"error": result_box["error"], "query": query, "count": 0, "results": [], "sources": []}
    return result_box.get("result", {"query": query, "count": 0, "results": [], "sources": []})


def _execute_sub_query_streaming(sub_query: Dict, allow_jit: bool = True) -> Generator[tuple, None, None]:
    """
    단일 하위 쿼리를 실행하고 (event, data) 튜플을 실시간으로 yield.
    마지막에 (None, data)를 yield하여 결과를 전달.
    """
    q_type = sub_query['type']
    query = sub_query['query']
    data = {}
    
    try:
        if q_type == 'file':
            # 파일 검색
            yield ({"type": "thinking", "content": f"🔍 [File] '{query}' 검색 중..."}, None)
            result = search_files(query, sort_by="date_newest")
            count = result.get("count", 0)
            yield ({"type": "tool_result", "name": "search_files", "args": {"pattern": query}, "result": result}, None)
            
            data = {"type": "file", "result": result}
            
            # --- Active Learning (JIT) ---
            if count > 0 and allow_jit:
                jit_events = []
                max_jit = _positive_int(_search_config().get("max_jit_files"), 5)
                _perform_jit_ingestion(result.get("results", [])[:max_jit], jit_events)
                for ev in jit_events:
                    yield (ev, None)

            if count > 0 and _search_config().get("enable_query_time_direct_read_fallback", True):
                yield ({"type": "thinking", "content": "📖 후보 파일을 직접 열어 내용을 확인합니다..."}, None)
                direct_results = direct_read_candidates(result.get("results", []), _search_config())
                result["direct_read_results"] = direct_results
                by_path = {r.get("path"): r for r in direct_results}
                for file_result in result.get("results", []):
                    path = file_result.get("path")
                    if path in by_path:
                        file_result["direct_read"] = by_path[path]
                ok_count = sum(1 for r in direct_results if r.get("success"))
                yield ({"type": "thinking", "content": f"✅ 직접 열람 완료: {ok_count}/{len(direct_results)}개 파일에서 텍스트 추출"}, None)

                ocr_cfg = _ocr_config()
                if ocr_cfg.get("enabled", True) and ocr_cfg.get("auto_on_direct_read", True):
                    ocr_targets = [r for r in direct_results if r.get("ocr_needed") and r.get("path")]
                    if ocr_targets:
                        yield ({"type": "thinking", "content": "PDF에서 텍스트를 찾지 못해 OCR 엔진으로 읽는 중입니다..."}, None)
                    for direct in ocr_targets:
                        path = direct.get("path")
                        fname = os.path.basename(path or "PDF")
                        yield ({"type": "thinking", "content": f"🔎 OCR 엔진으로 읽는 중: {fname}"}, None)
                        ocr_result = _ocr_pdf_for_direct_read(path, ocr_cfg)
                        if ocr_result.get("success"):
                            direct["content"] = ocr_result.get("content", "")
                            direct["success"] = True
                            direct["category"] = "ok"
                            direct["detail"] = ""
                            direct["ocr_applied"] = True
                            direct["ocr_needed"] = False
                            direct["source_engine"] = "ocr_direct_read"
                            yield ({"type": "thinking", "content": f"✅ OCR 완료: {fname}"}, None)
                        else:
                            errors = ocr_result.get("errors") or ["OCR failed"]
                            detail = str(errors[0])[:200]
                            direct["category"] = "ocr_error" if "timeout" not in detail.lower() else "timeout"
                            direct["detail"] = detail
                            yield ({"type": "thinking", "content": f"⚠️ OCR 처리에 실패해 추출 가능한 내용만 사용합니다: {fname} ({detail})"}, None)
            
        elif q_type == 'content':
            # 내용 검색
            yield ({"type": "thinking", "content": f"📚 [Content] '{query}' 검색 중..."}, None)
            
            # 벡터 인덱스가 비어 있는지 사전 확인
            try:
                from faiss_store import get_total_count
                total = get_total_count()
                if total == 0:
                    yield ({"type": "thinking", "content": "⚠️ 벡터 인덱스가 비어 있습니다. 문서 학습(ingest)이 필요합니다. 파일 검색 결과만 사용합니다."}, None)
                    data = {"type": "content", "result": {"query": query, "count": 0, "results": [], "sources": [], "empty_index": True}}
                    yield (None, data)
                    return
            except Exception:
                pass  # 인덱스 확인 실패 시 그대로 검색 진행
            
            result = _search_content_with_timeout(query, k=5)
            count = result.get("count", 0)
            yield ({"type": "thinking", "content": f"✅ [Content] 완료: {count}개 문서 발견"}, None)
            
            data = {"type": "content", "result": result}

        elif q_type == 'hybrid':
            yield ({"type": "thinking", "content": f"🔄 [Hybrid] '{query}' 검색 중..."}, None)
            result = search_hybrid(query, k=5)
            count = result.get("count", 0)
            yield ({"type": "thinking", "content": f"✅ [Hybrid] 완료: {count}개 결과 병합"}, None)
            data = {"type": "hybrid", "result": result}
            
    except Exception as e:
        yield ({"type": "error", "content": f"Sub-query Error: {e}"}, None)
    
    yield (None, data)

def _perform_jit_ingestion(file_results: List[Dict], events: List[Dict]):
    """
    검색된 파일 리스트를 확인하고 인덱싱되지 않은 경우 즉시 학습합니다.
    """
    try:
        # 1. 처리된 목록 로드
        processed_files = set()
        from runtime_paths import runtime_path
        processed_files_path = os.environ.get("PROCESSED_FILES_PATH", runtime_path("processed_files.txt"))
        if os.path.exists(processed_files_path):
            with open(processed_files_path, "r", encoding="utf-8", errors="ignore") as f:
                processed_files = set(line.strip() for line in f)
        
        missing = []
        for r in file_results:
            path = r.get("path")
            ext = os.path.splitext(path)[1].lower() if path else ""
            if path and path not in processed_files and ext in EMBEDDABLE_EXTENSIONS:
                missing.append(path)
        
        if missing:
            events.append({"type": "thinking", "content": f"🆕 미학습 파일 {len(missing)}개 발견! 즉시 학습을 시작합니다..."})
            embedder = BackgroundEmbedder()
            
            for index, path in enumerate(missing):
                fname = os.path.basename(path)
                events.append({"type": "thinking", "content": f"⚡ [JIT] '{fname}' 학습 중..."})
                result = embedder.process_single_file_synchronous(path)
                success = result.get("success") if isinstance(result, dict) else bool(result)
                if success:
                    events.append({"type": "thinking", "content": f"✅ [JIT] '{fname}' 완료"})
                else:
                    category = result.get("category", "unknown_error") if isinstance(result, dict) else "unknown_error"
                    detail = result.get("detail", "") if isinstance(result, dict) else ""
                    suffix = f" ({category})" if category else ""
                    if detail:
                        suffix += f": {str(detail)[:200]}"
                    events.append({"type": "error", "content": f"[JIT] '{fname}' 실패{suffix}"})
                    if category == "embedding_error":
                        remaining = len(missing) - index - 1
                        if remaining > 0:
                            events.append({
                                "type": "thinking",
                                "content": f"⏭️ VectorStore 로드 실패로 나머지 {remaining}개 파일 학습을 건너뜁니다.",
                            })
                        break
                     
    except Exception as e:
        events.append({"type": "thinking", "content": f"⚠️ JIT Error: {e}"})

def _synthesize_answer(question: str, execution_results: List[Dict], chat_history: List[tuple]) -> Generator:
    """
    모든 실행 결과를 종합하여 최종 답변을 생성합니다.
    """
    # 컨텍스트 조립
    context_parts = []
    source_info = []
    
    file_hit_count = 0
    content_hit_count = 0
    
    for item in execution_results:
        sq = item['sub_query']
        data = item['data']
        res = data.get("result", {})
        
        if data.get("type") == "file":
            count = res.get("count", 0)
            file_hit_count += count
            if count > 0:
                top_files = []
                direct_context_chars = _positive_int(_search_config().get("max_direct_read_context_chars"), 12000)
                used_direct_chars = 0
                per_file_context_chars = _positive_int(_search_config().get("max_direct_read_context_chars_per_file"), 3000)
                for r in res.get("results", [])[:5]:
                    line = f"📄 {r.get('name')} ({r.get('path')})"
                    direct = r.get("direct_read") or {}
                    if direct.get("success") and direct.get("content") and used_direct_chars < direct_context_chars:
                        remaining = max(0, direct_context_chars - used_direct_chars)
                        snippet = str(direct.get("content") or "")[: min(per_file_context_chars, remaining)]
                        used_direct_chars += len(snippet)
                        label = "직접 열람 내용 (OCR)" if direct.get("ocr_applied") else "직접 열람 내용"
                        line += f"\n[{label}]\n{snippet}"
                    elif direct:
                        category = direct.get("category") or "unknown_error"
                        detail = direct.get("detail") or ""
                        ocr_note = " OCR 필요." if direct.get("ocr_needed") else ""
                        line += f"\n[직접 열람 실패] {category}: {detail}{ocr_note}"
                    top_files.append(line)
                context_parts.append(f"=== 파일 검색 결과 (Query: {sq['query']}) ===\n" + "\n\n".join(top_files))
                
                # 소스 메타데이터
                for r in res.get("results", [])[:5]:
                    source_info.append({
                        "source": r.get("path"),
                        "type": "file",
                        "source_engine": (r.get("direct_read") or {}).get("source_engine", r.get("source_engine", "filename")),
                        "score": r.get("score"),
                        "metadata": r.get("metadata", {}),
                        "snippet": ((r.get("direct_read") or {}).get("content") or "")[:300],
                    })

        elif data.get("type") in ("content", "hybrid"):
            count = res.get("count", 0)
            content_hit_count += count
            if count > 0:
                docs = [f"--- 문서: {os.path.basename(d.get('source', 'Unknown'))} ---\n{d.get('content')[:1000]}" 
                        for d in res.get("results", [])[:4]]
                label = "하이브리드 검색 결과" if data.get("type") == "hybrid" else "내용 검색 결과"
                context_parts.append(f"=== {label} (Query: {sq['query']}) ===\n" + "\n\n".join(docs))
                
                # 소스 메타데이터
                for d in res.get("results", [])[:4]:
                    source_info.append({
                        "source": d.get("source"),
                        "type": "content",
                        "source_engine": d.get("source_engine", "vector"),
                        "score": d.get("score"),
                        "metadata": d.get("metadata", {}),
                        "snippet": d.get("content", "")[:300],
                    })

    if not context_parts:
        yield {
            "type": "answer", 
            "content": "죄송합니다. 관련된 정보를 찾을 수 없습니다. (검색 계획을 수행했지만 결과가 없습니다)",
            "sources": []
        }
        return

    full_context = "\n\n".join(context_parts)
    
    # 대화 기록 포맷팅
    history_str = ""
    if chat_history:
        for role, msg in chat_history[-5:]:
             history_str += f"{role}: {msg}\n"
    
    # 1차: Local Ollama provider (get_provider)
    answer = get_provider().synthesize(question, full_context, history_str)
    if answer:
        print(f"[Synthesis] Local provider success ({len(answer)} chars)")
        yield {
            "type": "answer",
            "content": answer,
            "sources": source_info,
            "source_count": len(source_info)
        }
        return
    
    # 2차: direct Ollama fallback
    print("[Synthesis] Local provider failed, falling back to direct Ollama...")
    llm_kwargs = {
        "model": LLM_MODEL,
        "base_url": OLLAMA_BASE_URL,
        "temperature": 0.2,
        "keep_alive": -1,
        "num_ctx": LLM_NUM_CTX,
        "num_predict": LLM_NUM_PREDICT,
    }
    if ChatOllama.__module__.startswith("langchain_ollama"):
        llm_kwargs["sync_client_kwargs"] = {"timeout": LLM_REQUEST_TIMEOUT}
    else:
        llm_kwargs["timeout"] = LLM_REQUEST_TIMEOUT
    llm = ChatOllama(**llm_kwargs)

    prompt = f"""Conversation History:
{history_str}

Current Question: {question}

Research Results:
{full_context[:6000]}

Instructions:
1. Synthesize the research results to answer the user's question.
2. Maintain the context of the conversation.
3. If files are found but content is missing, mention the file names explicitly.
4. Answer in Korean (한국어).
"""

    try:
        response = llm.invoke([
            SystemMessage(content="You are a helpful assistant. Synthesize the provided search results to answer."),
            HumanMessage(content=prompt)
        ])
        
        yield {
            "type": "answer",
            "content": response.content,
            "sources": source_info,
            "source_count": len(source_info)
        }
        
    except Exception as e:
        yield {"type": "error", "content": f"Synthesis Error: {e}"}

def simple_unified_chat(question: str) -> Dict:
    """테스트용 동기 래퍼"""
    events = []
    answer = ""
    for event in get_unified_response(question, stream=False):
        events.append(event)
        if event["type"] == "answer":
            answer = event["content"]
    return {"answer": answer, "events": events}

if __name__ == "__main__":
    # Test
    print(simple_unified_chat("2026년 유도등 매출 목표는?"))
