# -*- coding: utf-8 -*-
"""
Unified Engine (Azure Agentic Retrieval Architecture)
Microft Azure AI Search의 'Agentic Retrieval' 디자인 패턴을 적용.
Flow: Query -> Planner (LLM) -> Parallel Execution -> Synthesis -> Answer
"""
import json
import re
import os
import threading
from typing import Dict, Generator, List, Any
from concurrent.futures import ThreadPoolExecutor

try:
    from langchain_ollama import ChatOllama
except ImportError:  # Backward compatibility until requirements are installed.
    from langchain_community.chat_models import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage

from rag_engine import LLM_MODEL, OLLAMA_BASE_URL, LLM_NUM_CTX, LLM_NUM_PREDICT, LLM_REQUEST_TIMEOUT
from tools import search_files, search_content
from background_embedder import BackgroundEmbedder
from conversation_logger import conversation_logger
from ai_providers.provider_manager import get_provider
from config_manager import load_config

# --- Configuration ---
MAX_WORKERS = 3  # 병렬 실행 스레드 수
CONTENT_SEARCH_TIMEOUT = 60  # content search 최대 대기 시간(초)


def _search_config() -> Dict:
    return load_config().get("search", {})


def _significant_tokens(text: str) -> List[str]:
    stop = {"찾아줘", "찾아", "주세요", "최신", "최근", "파일", "자료", "관련", "대한", "있는", "알려줘"}
    tokens = re.findall(r"[0-9A-Za-z가-힣]{2,}", text or "")
    return [t for t in tokens if t not in stop][:5]


def _fallback_file_patterns(question: str) -> List[str]:
    tokens = _significant_tokens(question)
    patterns: List[str] = []
    if tokens:
        patterns.append("*" + "*".join(tokens) + "*")
        for token in tokens[:2]:
            patterns.append(f"*{token}*")
    if not patterns and question.strip():
        patterns.append("*" + question.strip()[:30] + "*")
    return list(dict.fromkeys(patterns))[:4]


def _normalize_plan_file_first(plan: Dict, question: str) -> Dict:
    sub_queries = list(plan.get("sub_queries", []))
    if not any(sq.get("type") == "file" for sq in sub_queries):
        for pattern in _fallback_file_patterns(question):
            sub_queries.insert(0, {"type": "file", "query": pattern, "reason": "캐시된 경로/파일명 우선 검색"})
    sub_queries.sort(key=lambda sq: 0 if sq.get("type") == "file" else 1)
    plan["sub_queries"] = sub_queries
    return plan

def get_unified_response(
    question: str,
    chat_history: List[tuple] = None,
    stream: bool = True
) -> Generator[Dict, None, None]:
    """
    통합 응답 생성기 (Agentic Flow).
    """
    # 1. Query Planning (계획 수립)
    yield {"type": "thinking", "content": "🤔 질문 분석 및 검색 계획 수립 중..."}
    
    plan = _plan_query(question, chat_history)
    
    if not plan or not plan.get("sub_queries"):
        yield {"type": "thinking", "content": "⚠️ 계획 수립 실패, 기본 검색으로 전환합니다."}
        # Fallback: cached filename search first, content search second.
        plan = {
            "sub_queries": [
                *[{"type": "file", "query": p, "reason": "Fallback file-first"} for p in _fallback_file_patterns(question)],
                {"type": "content", "query": question, "reason": "Fallback content"},
            ]
        }
    else:
        plan = _normalize_plan_file_first(plan, question)
        # 계획 표시
        plan_desc = "\n".join([f"- [{sq['type'].upper()}] {sq['query']} ({sq['reason']})" for sq in plan['sub_queries']])
        yield {"type": "thinking", "content": f"📋 검색 계획:\n{plan_desc}"}

    # 2. Execution (실행): cached file/path search first, content search only if needed.
    yield {"type": "thinking", "content": "🚀 캐시된 경로/파일명 검색을 먼저 실행합니다..."}
    
    execution_results = []
    
    file_queries = [sq for sq in plan["sub_queries"] if sq.get("type") == "file"]
    content_queries = [sq for sq in plan["sub_queries"] if sq.get("type") == "content"]

    def run_query(sq: Dict, allow_jit: bool = True) -> Dict:
        final_data = {}
        for event, data in _execute_sub_query_streaming(sq, allow_jit=allow_jit):
            if event is not None:
                yield event
            if data is not None:
                final_data = data
        return final_data

    for sq in file_queries:
        data = yield from run_query(sq, allow_jit=False)
        execution_results.append({"sub_query": sq, "data": data})

    file_count = sum(item["data"].get("result", {}).get("count", 0) for item in execution_results if item["data"].get("type") == "file")
    sufficient_count = int(_search_config().get("file_search_sufficient_count", 1))
    if file_count >= sufficient_count:
        yield {"type": "thinking", "content": f"✅ 캐시된 파일명/경로 검색에서 {file_count}개 발견. 임베딩 검색은 생략합니다."}
    else:
        yield {"type": "thinking", "content": "📚 파일명 결과가 부족하여 임베딩/내용 검색을 실행합니다..."}
        if not content_queries:
            content_queries = [{"type": "content", "query": question, "reason": "파일 검색 결과 부족 fallback"}]
        for sq in content_queries:
            data = yield from run_query(sq)
            execution_results.append({"sub_query": sq, "data": data})

    # 3. Active Learning (JIT Ingestion) implicitly handled in execution

    # 4. Synthesis (종합 및 답변)
    yield {"type": "thinking", "content": "🧠 수집된 정보를 종합하여 답변을 생성합니다..."}
    
    final_answer = ""
    for event in _synthesize_answer(question, execution_results, chat_history):
        if event['type'] == 'answer':
            final_answer = event['content']
        yield event
        
    # [Logging] 대화 기록 저장 (실패 분석용)
    is_success = "죄송합니다" not in final_answer and "정보를 찾을 수 없습니다" not in final_answer
    conversation_logger.log_interaction(
        question=question,
        plan=plan,
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
    result = get_provider().plan_query(question, history_str)
    if result and result.get("sub_queries"):
        print(f"[Plan] Local provider success: {len(result['sub_queries'])} sub-queries")
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
    
    system_prompt = f"""You are a Query Planner. Break down the user's request into actionable sub-queries.
You have access to the conversation history to resolve coreferences (e.g., 'it', 'that file', 'previous one').

Conversation History:
{history_str}

Output format: JSON only.
Structure:
{{
  "sub_queries": [
    {{"type": "file", "query": "filename_pattern", "reason": "why"}},
    {{"type": "content", "query": "search_keywords", "reason": "why"}}
  ]
}}

Rules:
1. If user refers to previous context (e.g. "What is the price of *that*?"), REPLACE 'that' with the actual subject from history.
2. Cached filename/path search is the first priority. Prefer "file" type first.
3. "file" type: Use for finding files. Glob pattern (e.g. *keyword*.xlsx, *catalog*).
4. For document-finding requests (file, catalog, quotation, drawing, manual, certificate), include file searches before content searches.
5. "content" type: Use only when the user asks about content/meaning/details that may require embedded document text.
6. If the user asks for "2026 Sales Plan", include a file search first and content search second.
"""
    
    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=question)
        ])
        return json.loads(response.content)
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
                max_jit = int(_search_config().get("max_jit_files", 5))
                _perform_jit_ingestion(result.get("results", [])[:max_jit], jit_events)
                for ev in jit_events:
                    yield (ev, None)
            
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
            if path and path not in processed_files and ext in ['.pdf', '.docx', '.pptx', '.xlsx', '.hwp', '.txt']:
                missing.append(path)
        
        if missing:
            events.append({"type": "thinking", "content": f"🆕 미학습 파일 {len(missing)}개 발견! 즉시 학습을 시작합니다..."})
            embedder = BackgroundEmbedder()
            
            for path in missing:
                fname = os.path.basename(path)
                events.append({"type": "thinking", "content": f"⚡ [JIT] '{fname}' 학습 중..."})
                if embedder.process_single_file_synchronous(path):
                     events.append({"type": "thinking", "content": f"✅ [JIT] '{fname}' 완료"})
                else:
                     events.append({"type": "thinking", "content": f"⚠️ [JIT] '{fname}' 실패"})
                     
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
                top_files = [f"📄 {r.get('name')} ({r.get('path')})" for r in res.get("results", [])[:5]]
                context_parts.append(f"=== 파일 검색 결과 (Query: {sq['query']}) ===\n" + "\n".join(top_files))
                
                # 소스 메타데이터
                for r in res.get("results", [])[:5]:
                    source_info.append({"source": r.get("path"), "type": "file"})

        elif data.get("type") == "content":
            count = res.get("count", 0)
            content_hit_count += count
            if count > 0:
                docs = [f"--- 문서: {os.path.basename(d.get('source', 'Unknown'))} ---\n{d.get('content')[:1000]}" 
                        for d in res.get("results", [])[:4]]
                context_parts.append(f"=== 내용 검색 결과 (Query: {sq['query']}) ===\n" + "\n\n".join(docs))
                
                # 소스 메타데이터
                for d in res.get("results", [])[:4]:
                    source_info.append({"source": d.get("source"), "type": "content"})

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
