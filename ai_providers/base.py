# -*- coding: utf-8 -*-
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Generator, List, Optional


class AIProvider(ABC):
    name = "base"

    @abstractmethod
    def health_check(self) -> Dict:
        """Return provider availability and current model details."""

    @abstractmethod
    def plan_query(self, question: str, history_str: str = "") -> Optional[dict]:
        """Create a structured search plan."""

    @abstractmethod
    def synthesize(self, question: str, context: str, history_str: str = "") -> Optional[str]:
        """Synthesize final answer from retrieved context."""

    @abstractmethod
    def agent_response(self, question: str, chat_history: List[tuple] = None) -> Generator[Dict, None, None]:
        """Generate an agent/tool-use response stream."""
