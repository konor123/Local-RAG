# -*- coding: utf-8 -*-
"""
Gemini Client - Google Gemini API 래퍼
Query Planning과 Answer Synthesis에 사용합니다.
Ollama를 폴백으로 유지합니다.
"""
import json
import os
from typing import Optional

# API Configuration
# Legacy Gemini support is disabled unless explicitly configured by the user.
# Do not provide a source-code default for API keys.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_PRIMARY_MODEL = os.environ.get("GEMINI_PRIMARY_MODEL", "gemini-3.5-flash")
GEMINI_FALLBACK_MODEL = "gemini-2.5-flash"

_client = None

def _get_client():
    """Gemini 클라이언트 싱글톤"""
    global _client
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured. Local Ollama provider is the default for this project.")
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def gemini_plan_query(question: str, history_str: str = "") -> Optional[dict]:
    """
    Gemini로 검색 계획을 수립합니다.
    
    Returns:
        dict: {"sub_queries": [...]} or None on failure
    """
    system_prompt = f"""You are a Query Planner for a Korean company's internal document search system.
Break down the user's request into actionable sub-queries.
You have access to the conversation history to resolve coreferences (e.g., 'it', 'that file', 'previous one').

Conversation History:
{history_str}

Output format: JSON only. No markdown, no code blocks. Just raw JSON.
Structure:
{{
  "sub_queries": [
    {{"type": "file", "query": "filename_pattern", "reason": "why"}},
    {{"type": "content", "query": "search_keywords", "reason": "why"}}
  ]
}}

Rules:
1. If user refers to previous context (e.g. "What is the price of *that*?"), REPLACE 'that' with the actual subject from history.
2. "file" type: Use for finding files. Glob pattern (e.g. *keyword*.xlsx).
3. "content" type: Use for searching document contents with semantic search.
4. Always include a "content" type search for the core question.
5. If the user mentions a specific document type or filename, add a "file" type search too.
6. Generate Korean keywords for Korean questions.
7. Maximum 4 sub-queries.
8. Handle easy-to-confuse Korean characters: if a query contains '앤' or '엔' (e.g., '건국이앤아이'), you MUST generate queries for BOTH spellings (e.g., '건국이앤아이' and '건국이엔아이')."""
    
    try:
        client = _get_client()
        try:
            response = client.models.generate_content(
                model=GEMINI_PRIMARY_MODEL,
                contents=question,
                config={
                    "system_instruction": system_prompt,
                    "temperature": 0,
                    "response_mime_type": "application/json",
                }
            )
        except Exception as e:
            print(f"[Gemini] Primary plan failed ({e}), falling back to 2.5-flash...")
            response = client.models.generate_content(
                model=GEMINI_FALLBACK_MODEL,
                contents=question,
                config={
                    "system_instruction": system_prompt,
                    "temperature": 0,
                    "response_mime_type": "application/json",
                }
            )
        
        result_text = response.text.strip()
        return json.loads(result_text)
        
    except Exception as e:
        print(f"[Gemini] Plan Error: {e}")
        return None


def gemini_synthesize(question: str, context: str, history_str: str = "") -> Optional[str]:
    """
    Gemini로 검색 결과를 종합하여 답변을 생성합니다.
    
    Returns:
        str: 한국어 답변 or None on failure
    """
    system_prompt = """You are an AI assistant for a Korean company called OSL ENG (오에스엘이엔지).
You answer questions based ONLY on the provided search results.
Always answer in Korean (한국어).
If the search results don't contain relevant information, say so honestly.
When mentioning files, include the full filename."""
    
    prompt = f"""Conversation History:
{history_str}

Current Question: {question}

Research Results:
{context[:8000]}

Instructions:
1. Synthesize the research results to answer the user's question.
2. Maintain the context of the conversation.
3. If files are found but content is missing, mention the file names explicitly.
4. Answer in Korean (한국어).
5. Be concise and direct."""
    
    try:
        client = _get_client()
        try:
            response = client.models.generate_content(
                model=GEMINI_PRIMARY_MODEL,
                contents=prompt,
                config={
                    "system_instruction": system_prompt,
                    "temperature": 0.3,
                }
            )
        except Exception as e:
            print(f"[Gemini] Primary synthesis failed ({e}), falling back to 2.5-flash...")
            response = client.models.generate_content(
                model=GEMINI_FALLBACK_MODEL,
                contents=prompt,
                config={
                    "system_instruction": system_prompt,
                    "temperature": 0.3,
                }
            )
        
        return response.text.strip()
        
    except Exception as e:
        print(f"[Gemini] Synthesis Error: {e}")
        return None


if __name__ == "__main__":
    # Quick test
    print("Testing Gemini connection...")
    result = gemini_plan_query("유도등 설치 기준 알려줘")
    if result:
        print(f"✅ Plan: {json.dumps(result, ensure_ascii=False, indent=2)}")
    else:
        print("❌ Plan failed")
    
    answer = gemini_synthesize(
        "유도등 설치 기준",
        "유도등은 건축물 내부에서 화재 등 비상 시 피난방향을 안내하는 조명장치입니다."
    )
    if answer:
        print(f"✅ Answer: {answer}")
    else:
        print("❌ Synthesis failed")
