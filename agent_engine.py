# -*- coding: utf-8 -*-
"""
Agentic Engine with local Ollama tool calling.
"""
import os
import json
import re
from typing import List, Dict, Optional, Generator
from tools import TOOL_DEFINITIONS, execute_tool
from config_manager import get_local_ai_config, load_config
from search_terms import extract_search_tokens

# Configuration
MAX_TOOL_CALLS = 10


def _model_supports_tools(model: str, local_ai: Dict = None) -> bool:
    """Return whether a configured local Ollama model supports tool calling."""
    local_ai = local_ai or get_local_ai_config()
    capabilities = local_ai.get("model_capabilities", {})
    base = model.rsplit(":", 1)[0]  # Normalize: "exaone3.5:7b" -> "exaone3.5"
    model_caps = (
        capabilities.get(model)
        or capabilities.get(model.removesuffix(":latest"))
        or capabilities.get(base)
        or {}
    )
    return bool(model_caps.get("tools", True))


def _select_tool_model(model: str, local_ai: Dict) -> tuple[str, bool]:
    """Select a tool-capable model, falling back when the chosen model lacks tools."""
    if _model_supports_tools(model, local_ai):
        return model, False
    fallback = local_ai.get("fallback_agent_model")
    if fallback and fallback != model and _model_supports_tools(fallback, local_ai):
        return fallback, True
    return model, False


def _model_in_list(model: str, model_list: List[str]) -> bool:
    base = model.rsplit(":", 1)[0]
    aliases = {model, model.removesuffix(":latest"), base}
    return any(item in aliases or item.rsplit(":", 1)[0] in aliases for item in model_list)


def _uses_manual_agent(model: str, local_ai: Dict) -> bool:
    """Return whether this model should use prompt-based manual search routing."""
    manual_models = local_ai.get("manual_agent_models", [])
    return _model_in_list(model, manual_models) or not _model_supports_tools(model, local_ai)


def _ollama_plain_chat(
    messages: List[Dict],
    model: str,
    base_url: str,
    local_ai: Dict,
    temperature: float = 0.0,
) -> str:
    """Call Ollama chat without native tool definitions."""
    import requests

    response = requests.post(
        f"{base_url}/api/chat",
        json={
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_thread": 10,
                "num_ctx": local_ai["num_ctx"],
                "num_predict": local_ai["num_predict"],
            },
        },
        timeout=local_ai["request_timeout"],
    )
    if response.status_code != 200:
        raise RuntimeError(f"Ollama error: {response.status_code} {response.text[:300]}")
    return response.json().get("message", {}).get("content", "")


def _parse_json_object(text: str) -> Optional[Dict]:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except Exception:
            return None


def _fallback_manual_tool_calls(question: str) -> List[Dict]:
    keywords = extract_search_tokens(question, limit=3) or [question.strip()[:30] or "*"]
    pattern = "*" + "*".join(keywords) + "*"
    query = " ".join(keywords)
    return [
        {"name": "search_files", "args": {"pattern": pattern, "sort_by": "date_newest"}},
        {"name": "search_content", "args": {"query": query, "k": 4}},
    ]


def _file_search_sufficient_count() -> int:
    try:
        return int(load_config().get("search", {}).get("file_search_sufficient_count", 1))
    except Exception:
        return 1


def _coerce_manual_tool_calls(plan: Optional[Dict], question: str) -> List[Dict]:
    allowed = {"search_files", "search_content"}
    raw_calls = []
    if isinstance(plan, dict):
        raw_calls = plan.get("tool_calls") or plan.get("tools") or plan.get("actions") or []
    calls = []
    for item in raw_calls[:4]:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("tool")
        args = item.get("args") or item.get("arguments") or {}
        if name not in allowed or not isinstance(args, dict):
            continue
        if name == "search_files":
            pattern = args.get("pattern") or args.get("query") or question
            pattern = str(pattern).strip()
            if pattern and not any(ch in pattern for ch in "*?"):
                pattern = f"*{pattern}*"
            calls.append({"name": name, "args": {"pattern": str(pattern), "sort_by": args.get("sort_by", "date_newest")}})
        elif name == "search_content":
            query = args.get("query") or args.get("keyword") or question
            try:
                k = int(args.get("k", 4))
            except (TypeError, ValueError):
                k = 4
            calls.append({"name": name, "args": {"query": str(query), "k": max(1, min(k, 8))}})
    return calls or _fallback_manual_tool_calls(question)


def _manual_search_agent_response(
    question: str,
    chat_history: List[tuple],
    model: str,
    base_url: str,
    local_ai: Dict,
) -> Generator[Dict, None, None]:
    """Prompt-routed search agent for models without native Ollama tool calling."""
    history = ""
    if chat_history:
        history = "\n".join(f"{role}: {content}" for role, content in chat_history[-6:])

    yield {"type": "thinking", "content": f"[MANUAL] {model} 모델은 네이티브 도구 호출 대신 프롬프트 기반 검색 라우팅을 사용합니다."}

    planner_messages = [
        {"role": "system", "content": """당신은 OSL 내부문서 검색 라우터입니다.
사용자 질문을 검색 도구 호출 JSON으로 변환하세요.
허용 도구는 search_files, search_content 뿐입니다.
반드시 JSON만 출력하세요. 설명이나 마크다운은 금지합니다.
형식:
{"tool_calls":[{"name":"search_files","args":{"pattern":"*키워드*","sort_by":"date_newest"}},{"name":"search_content","args":{"query":"키워드","k":4}}]}
파일명/카탈로그/견적서/증명서/도면/매뉴얼/브로셔 찾기는 search_files를 먼저 포함하세요.
내용 질문은 search_content를 보조로 포함하세요. 실제 실행은 파일 검색 결과가 없을 때만 수행됩니다.
최대 3개 호출만 만드세요."""},
        {"role": "user", "content": f"대화 기록:\n{history}\n\n현재 질문: {question}"},
    ]

    try:
        plan_text = _ollama_plain_chat(planner_messages, model, base_url, local_ai, temperature=0.0)
        plan = _parse_json_object(plan_text)
    except Exception as exc:
        yield {"type": "thinking", "content": f"[MANUAL] 검색 계획 생성 실패, 규칙 기반 검색으로 전환합니다: {exc}"}
        plan = None

    tool_calls = _coerce_manual_tool_calls(plan, question)
    tool_calls.sort(key=lambda c: 0 if c.get("name") == "search_files" else 1)
    tool_results = []
    file_hit_count = 0
    for call in tool_calls:
        name = call["name"]
        args = call["args"]
        if name == "search_content" and file_hit_count >= _file_search_sufficient_count():
            yield {"type": "thinking", "content": f"✅ 캐시된 파일명/경로 검색에서 {file_hit_count}개 발견. 임베딩 검색은 생략합니다."}
            continue
        yield {"type": "tool_call", "name": name, "args": args}
        result = execute_tool(name, args)
        tool_results.append({"name": name, "args": args, "result": result})
        yield {"type": "tool_result", "name": name, "result": result}
        if name == "search_files" and isinstance(result, dict):
            file_hit_count += int(result.get("count", 0) or 0)

    results_text = json.dumps(tool_results, ensure_ascii=False, default=str)[:8000]
    synthesis_messages = [
        {"role": "system", "content": """당신은 OSL 회사의 내부 문서 검색 도우미입니다.
검색 결과에 근거해서만 한국어로 답하세요.
파일이 있으면 파일명과 경로를 포함하고, 결과가 부족하면 부족하다고 말하세요."""},
        {"role": "user", "content": f"대화 기록:\n{history}\n\n질문: {question}\n\n검색 결과 JSON:\n{results_text}"},
    ]

    try:
        answer = _ollama_plain_chat(synthesis_messages, model, base_url, local_ai, temperature=0.2).strip()
        yield {"type": "answer", "content": answer, "tool_results": tool_results}
    except Exception as exc:
        yield {"type": "error", "content": str(exc)}

# System prompt (shared between Gemini and Ollama)
SYSTEM_PROMPT = """당신은 OSL 회사의 네트워크 드라이브를 탐색하고 파일을 찾아주는 AI 어시스턴트입니다.

사용자의 질문에 답하기 위해 다음 도구들을 사용할 수 있습니다:
- search_files: 파일명으로 파일 검색 (패턴 예: *견적*, *삼성*.xlsx)
  - sort_by 옵션: "name"(이름순), "date_newest"(최신순), "date_oldest"(오래된순)
- read_file: 파일 내용 읽기
- grep_content: 파일 내용에서 키워드 검색
- list_directory: 폴더 내용 나열
- save_memory: 중요한 정보 기억하기
- recall_memory: 기억한 정보 떠올리기
- search_content: RAG 벡터 검색으로 문서 내용 검색

## 중요: 지능형 검색 전략

1. **"최신", "가장 최근", "새로운" 요청 시**: sort_by="date_newest" 사용
2. **"오래된", "예전" 요청 시**: sort_by="date_oldest" 사용
3. **검색 결과가 0개일 때 자동 재시도**:
   - 첫 시도: 구체적 패턴 → 재시도: 키워드 분리 → 재시도: 단일 키워드
   - '앤'과 '엔'처럼 혼동하기 쉬운 문자가 포함된 검색어(예: "건국이앤아이")는 "건국이엔아이"와 같이 다른 표기로도 재검색을 시도하세요.
4. **결과 보고 시 경로 포함**: 찾은 파일의 전체 경로를 알려주세요.

## 기본 규칙
- 한국어로 친절하게 답변하세요
- 검색 결과가 많으면 가장 관련성 높은 것들만 설명하세요
- 사용자가 중요한 정보를 알려주면 save_memory로 저장하세요

검색 대상: 현재 연결된 로컬/네트워크 드라이브 전체(시스템 폴더 제외)"""

def _ollama_agent_response(
    question: str,
    chat_history: List[tuple] = None,
    model: str = None,
    base_url: str = None,
) -> Generator[Dict, None, None]:
    """Ollama 기반 에이전트 응답"""
    import requests
    
    local_ai = get_local_ai_config()
    OLLAMA_BASE_URL = base_url or local_ai["base_url"]
    requested_model = model or local_ai["model"]
    if _uses_manual_agent(requested_model, local_ai):
        yield from _manual_search_agent_response(question, chat_history, requested_model, OLLAMA_BASE_URL, local_ai)
        return

    LLM_MODEL, used_fallback = _select_tool_model(requested_model, local_ai)
    if used_fallback:
        yield {
            "type": "thinking",
            "content": f"[WARN] {requested_model} 모델은 도구 호출을 지원하지 않아 {LLM_MODEL} 모델로 에이전트 작업을 수행합니다."
        }
    elif not _model_supports_tools(LLM_MODEL, local_ai):
        yield {
            "type": "error",
            "content": f"선택한 모델({LLM_MODEL})은 도구 호출을 지원하지 않습니다. 도구 지원 모델을 선택하거나 OSL_RAG_AGENT_MODEL을 설정하세요."
        }
        return
    
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    if chat_history:
        for role, content in chat_history[-6:]:
            messages.append({"role": role, "content": content})
    
    messages.append({"role": "user", "content": question})
    
    tool_calls_count = 0
    tool_results = []
    
    while tool_calls_count < MAX_TOOL_CALLS:
        try:
            response = requests.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": LLM_MODEL,
                    "messages": messages,
                    "tools": TOOL_DEFINITIONS,
                    "stream": False,
                    "options": {
                        "temperature": 0.2,
                        "num_thread": 10,
                        "num_ctx": local_ai["num_ctx"],
                        "num_predict": local_ai["num_predict"],
                    }
                },
                timeout=local_ai["request_timeout"]
            )
            
            if response.status_code != 200:
                detail = response.text[:300]
                yield {"type": "error", "content": f"Ollama error: {response.status_code} {detail}"}
                return
            
            result = response.json()
            message = result.get("message", {})
            tool_calls = message.get("tool_calls", [])
            
            if tool_calls:
                for tool_call in tool_calls:
                    tool_name = tool_call["function"]["name"]
                    tool_args = tool_call["function"]["arguments"]
                    
                    if isinstance(tool_args, str):
                        try:
                            tool_args = json.loads(tool_args)
                        except:
                            tool_args = {}
                    
                    tool_calls_count += 1
                    
                    if tool_name == "search_files":
                        pattern = tool_args.get("pattern", "")
                        sort_by = tool_args.get("sort_by", "name")
                        sort_text = {"name": "이름순", "date_newest": "최신순", "date_oldest": "오래된순"}.get(sort_by, sort_by)
                        yield {"type": "thinking", "content": f"🤖 Ollama 전략: 패턴 '{pattern}' | 정렬: {sort_text}"}
                    
                    yield {"type": "tool_call", "name": tool_name, "args": tool_args}
                    
                    tool_result = execute_tool(tool_name, tool_args)
                    tool_results.append({"name": tool_name, "result": tool_result})
                    
                    if tool_name == "search_files":
                        count = tool_result.get("count", 0) if isinstance(tool_result, dict) else 0
                        if count > 0:
                            yield {"type": "thinking", "content": f"✅ 검색 성공: {count}개 발견"}
                        else:
                            yield {"type": "thinking", "content": f"❌ 검색 실패: 0개. 패턴 변경 시도..."}
                    
                    yield {"type": "tool_result", "name": tool_name, "result": tool_result}
                    
                    messages.append({"role": "assistant", "content": "", "tool_calls": [tool_call]})
                    messages.append({
                        "role": "tool",
                        "content": json.dumps(tool_result, ensure_ascii=False, default=str)
                    })
                continue
            else:
                content = message.get("content", "")
                if content:
                    yield {"type": "answer", "content": content, "tool_results": tool_results}
                return
                
        except Exception as e:
            yield {"type": "error", "content": str(e)}
            return
    
    yield {"type": "error", "content": f"Too many tool calls ({MAX_TOOL_CALLS} max)"}


def get_agent_response(
    question: str,
    chat_history: List[tuple] = None,
    stream: bool = True
) -> Generator[Dict, None, None]:
    """
    통합 에이전트 응답 (Local Ollama).
    """
    local_ai = get_local_ai_config()
    route = "manual search routing" if _uses_manual_agent(local_ai["model"], local_ai) else "native tool calling"
    print(f"[Agent] Using local Ollama model {local_ai['model']} with {route}...")
    yield from _ollama_agent_response(question, chat_history, model=local_ai["model"])


def simple_chat(question: str, chat_history: List[tuple] = None) -> Dict:
    """동기식 래퍼"""
    tool_calls = []
    tool_results = []
    answer = ""
    
    for event in get_agent_response(question, chat_history, stream=False):
        if event["type"] == "tool_call":
            tool_calls.append(event)
        elif event["type"] == "tool_result":
            tool_results.append(event)
        elif event["type"] == "answer":
            answer = event["content"]
        elif event["type"] == "error":
            answer = f"오류가 발생했습니다: {event['content']}"
    
    return {
        "answer": answer,
        "tool_calls": tool_calls,
        "tool_results": tool_results
    }


if __name__ == "__main__":
    print("Testing Gemini Agent Engine...")
    result = simple_chat("X드라이브에 어떤 폴더가 있어?")
    print(f"\nAnswer: {result['answer']}")
    print(f"Tool calls: {len(result['tool_calls'])}")
