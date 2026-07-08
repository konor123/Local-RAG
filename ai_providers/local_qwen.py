# -*- coding: utf-8 -*-
"""Local Ollama provider for EXAONE primary and Qwen adviser models."""
from __future__ import annotations

import json
import re
from typing import Dict, Generator, List, Optional

import requests
try:
    from langchain_ollama import ChatOllama
except ImportError:  # Backward compatibility until requirements are installed.
    from langchain_community.chat_models import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from config_manager import get_local_ai_config
from .base import AIProvider


class LocalQwenProvider(AIProvider):
    name = "local_qwen"

    def __init__(self):
        cfg = get_local_ai_config()
        self.model = cfg["model"]
        self.adviser_model = cfg.get("adviser_model") or self.model
        self.base_url = cfg["base_url"].rstrip("/")
        self.num_ctx = cfg.get("num_ctx", 4096)
        self.num_predict = cfg.get("num_predict", 512)
        self.request_timeout = cfg.get("request_timeout", 180)

    def health_check(self) -> Dict:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=3)
            response.raise_for_status()
            models = [m.get("name") for m in response.json().get("models", [])]
            adviser_ok = self.adviser_model in models if self.adviser_model != self.model else True
            return {
                "ok": self.model in models,
                "adviser_ok": adviser_ok,
                "provider": self.name,
                "model": self.model,
                "adviser_model": self.adviser_model,
                "base_url": self.base_url,
                "installed_models": models,
            }
        except Exception as exc:
            return {"ok": False, "provider": self.name, "model": self.model, "base_url": self.base_url, "error": str(exc)}

    def _llm(self, *, temperature: float = 0.0, format: str = None, model: str = None):
        kwargs = {
            "model": model or self.model,
            "base_url": self.base_url,
            "temperature": temperature,
            "keep_alive": -1,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
        }
        if ChatOllama.__module__.startswith("langchain_ollama"):
            kwargs["sync_client_kwargs"] = {"timeout": self.request_timeout}
        else:
            kwargs["timeout"] = self.request_timeout
        if format:
            kwargs["format"] = format
        return ChatOllama(**kwargs)

    def _chat_content(self, messages: List[Dict], *, model: str = None, temperature: float = 0.0, format: str = None) -> str:
        """Call Ollama directly and disable thinking output for adviser models that support it."""
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "think": False,  # Disables Qwen3 thinking output; ignored by non-Qwen3 models.
            "options": {
                "temperature": temperature,
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict,
            },
        }
        if format:
            payload["format"] = format
        response = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.request_timeout)
        response.raise_for_status()
        return response.json().get("message", {}).get("content", "")

    @staticmethod
    def _parse_json(text: str) -> Optional[dict]:
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

    def plan_query(self, question: str, history_str: str = "") -> Optional[dict]:
        system_prompt = f"""당신은 한국 회사 내부문서 검색 시스템의 검색 전략 수립기입니다.
대화 기록을 참고해 사용자의 질문에 검색 도구가 필요한지, 필요하다면 어떤 도구를 어떤 순서로 쓸지 결정하세요.

대화 기록:
{history_str}

반드시 JSON만 출력하세요. 마크다운 코드블록은 쓰지 마세요.
허용 형식 1 — 검색 없이 바로 답변 가능한 경우:
{{
  "mode": "direct",
  "reason": "검색이 필요 없는 이유"
}}

허용 형식 2 — 검색 도구가 필요한 경우:
{{
  "mode": "tools",
  "sub_queries": [
    {{"type": "file", "query": "파일명_패턴", "reason": "이유"}},
    {{"type": "content", "query": "검색 키워드", "reason": "이유"}},
    {{"type": "hybrid", "query": "검색 키워드", "reason": "이유"}}
  ]
}}

규칙:
1. 검색 전략은 당신이 결정합니다. file 검색을 기본값으로 삼지 말고 질문 목적에 맞게 도구와 순서를 선택하세요.
2. mode="direct"는 인사, 앱 사용법, 내부 문서 근거가 필요 없는 일반 질문에만 사용하세요.
3. file: 특정 파일명/경로/확장자를 찾을 때 사용합니다. query는 glob 패턴으로 작성하세요. 예: "*종합*카탈로그*", "*견적*", "*.pdf".
4. content: 문서 본문 의미/세부사항/요약/기준을 확인해야 할 때 사용합니다.
5. hybrid: 파일명과 본문 의미를 함께 찾아야 할 때 사용하는 도구입니다. hybrid는 라우팅 모드가 아니라 도구 중 하나입니다.
6. 사용자가 결과 부족을 암시하거나 이전 답변이 틀렸다고 하면 다른 키워드/도구 조합으로 재검색 계획을 세우세요.
7. 최대 4개 하위 쿼리만 만들고, 실행하고 싶은 순서대로 나열하세요.
8. 한국어 질문에는 한국어 핵심 키워드를 사용하세요.
9. '앤'과 '엔'처럼 혼동되는 표기가 있으면 둘 다 고려하세요.
"""
        try:
            content = self._chat_content(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                model=self.adviser_model,
                temperature=0,
                format="json",
            )
            return self._parse_json(content)
        except Exception as exc:
            print(f"[LocalProvider] Plan Error: {exc}")
            return None

    def synthesize(self, question: str, context: str, history_str: str = "") -> Optional[str]:
        system_prompt = """당신은 오에스엘이엔지(OSL ENG)의 내부 문서 검색 도우미입니다.
반드시 제공된 검색 결과에 근거해 한국어로 답하세요. 근거가 부족하면 부족하다고 말하세요.
파일을 언급할 때는 파일명을 포함하세요."""
        prompt = f"""대화 기록:
{history_str}

현재 질문: {question}

검색 결과:
{context[:8000]}

지시:
1. 검색 결과를 종합해 간결하게 답하세요.
2. 검색 결과에 없는 내용은 추측하지 마세요.
3. 관련 파일이 있으면 파일명을 명시하세요.
"""
        try:
            response = self._llm(temperature=0.2).invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=prompt),
            ])
            return response.content.strip()
        except Exception as exc:
            print(f"[LocalProvider] Synthesis Error: {exc}")
            return None

    def agent_response(self, question: str, chat_history: List[tuple] = None) -> Generator[Dict, None, None]:
        from agent_engine import _ollama_agent_response

        yield from _ollama_agent_response(question, chat_history, model=self.model, base_url=self.base_url)
