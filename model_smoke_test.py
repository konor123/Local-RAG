"""Local Ollama smoke checks for planner, synthesis, and tool-calling paths.

Run manually after starting Ollama and installing the selected model:
    python model_smoke_test.py
"""
import json
import sys

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from ai_providers.provider_manager import get_provider


def main():
    provider = get_provider()
    health = provider.health_check()
    print("Testing provider:", json.dumps(health, ensure_ascii=False, indent=2))

    plan = provider.plan_query("테스트용으로 PDF 파일을 찾아줘")
    print("planner:", json.dumps(plan, ensure_ascii=False, indent=2) if plan else "FAILED")

    answer = provider.synthesize("테스트 질문", "테스트 문맥입니다.")
    print("synthesis:", answer or "FAILED")

    try:
        from agent_engine import get_agent_response
        events = []
        for event in get_agent_response("테스트: 파일 검색 도구를 쓰지 말고 짧게 인사만 해줘", []):
            events.append(event)
            if event.get("type") in ("answer", "error"):
                break
        print("agent:", json.dumps(events, ensure_ascii=False, default=str, indent=2))
    except Exception as exc:
        print(f"agent: FAILED ({exc})")


if __name__ == "__main__":
    main()
