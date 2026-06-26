# -*- coding: utf-8 -*-
"""
Conversation Logger - 대화 및 시스템 동작 로그 기록기 (실패 분석용)
"""
import json
import os
import datetime
import re
from typing import Dict, Any
from runtime_paths import logs_dir

SENSITIVE_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"\b\d{8,12}:[0-9A-Za-z_\-]{20,}\b"),
    re.compile(r"(?i)(access_token|refresh_token|authorization_code|api_key|token)\s*[:=]\s*[^\s,;}]+"),
]

def redact_sensitive(value):
    if isinstance(value, str):
        redacted = value
        for pattern in SENSITIVE_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted
    if isinstance(value, dict):
        return {k: redact_sensitive(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_sensitive(v) for v in value]
    return value

class ConversationLogger:
    def __init__(self, log_dir=None):
        log_dir = log_dir or logs_dir()
        self.log_dir = log_dir
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
            
    def log_interaction(self, 
                        question: str, 
                        plan: Dict[str, Any], 
                        answer: str,
                        success: bool = True):
        """
        사용자 요청, AI 계획, 결과를 JSONL 파일로 기록합니다.
        """
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        log_file = os.path.join(self.log_dir, f"chat_log_{today}.jsonl")
        
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "question": redact_sensitive(question),
            "success": success,
            "failed_reason": "No relevant info found" if not success else None,
            "plan": redact_sensitive(plan),
            "answer": redact_sensitive(answer)
        }
        
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"Logging Failed: {e}")

# 싱글톤 인스턴스
conversation_logger = ConversationLogger()
