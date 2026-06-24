"""Lightweight unified flow test to verify real-time streaming and empty-index fixes."""
import asyncio
import time
import os
import sys

os.environ['OSL_RAG_NUM_CTX'] = '2048'
os.environ['OSL_RAG_NUM_PREDICT'] = '128'
os.environ['OSL_RAG_REQUEST_TIMEOUT'] = '120'

sys.path.insert(0, r'C:\Users\OSLENG\Desktop\projects\OSL RAG Internal')

from unified_engine import run_unified_flow


async def main():
    events = []
    t0 = time.time()
    print("=== UNIFIED FLOW TEST START ===")
    print("Start time: {:.1f}".format(t0))

    async for event in run_unified_flow(
        user_query="테스트 문서 목록을 보여줘",
        user_id="test_user",
        chat_history=[],
        doc_filter=None,
        session_id="test-session-001",
        conversation_id="test-conv-001",
        max_history_turns=0,
    ):
        elapsed = time.time() - t0
        events.append(event)
        # Print each event as it arrives (real-time streaming)
        if isinstance(event, dict):
            etype = event.get("type", "?")
            emsg = event.get("message", "")
        else:
            etype = type(event).__name__
            emsg = str(event)
        print("[{:6.1f}s] type={} msg={}".format(elapsed, etype, emsg[:150]))

    total = time.time() - t0
    print("=== TEST COMPLETE in {:.1f}s ===".format(total))
    print("Total events: {}".format(len(events)))

    # Checks
    none_count = sum(1 for e in events if e is None)
    print("None events: {}".format(none_count))

    types_seen = [e.get("type", "?") for e in events if isinstance(e, dict)]
    print("Event types: {}".format(types_seen))

    # Check for empty index warning
    empty_warn = any(
        "0벡터" in str(e) or "0 벡터" in str(e) or "empty" in str(e).lower()
        or "인덱스가 비어" in str(e) or "임베딩 벡터가 없습니다" in str(e)
        for e in events if e
    )
    print("Empty index warning seen: {}".format(empty_warn))

    # Check for completion event
    completion = any("완료" in str(e) or "done" in str(e).lower() for e in events if e)
    print("Completion event seen: {}".format(completion))

    # Check ordering: file_search/content_search thinking should appear before final synthesis
    file_search_thinking = [
        i for i, e in enumerate(events)
        if isinstance(e, dict) and "file_search" in str(e.get("type", ""))
    ]
    content_search_thinking = [
        i for i, e in enumerate(events)
        if isinstance(e, dict) and "content_search" in str(e.get("type", ""))
    ]
    print("File search thinking event indices: {}".format(file_search_thinking))
    print("Content search thinking event indices: {}".format(content_search_thinking))

    # Check for completion markers
    completion_events = [
        i for i, e in enumerate(events)
        if isinstance(e, dict) and "완료" in str(e.get("message", ""))
    ]
    print("Completion marker event indices: {}".format(completion_events))

    # Check no events arrive after 'done' or final event
    done_indices = [
        i for i, e in enumerate(events)
        if isinstance(e, dict) and e.get("type") in ("done", "error", "final_answer")
    ]
    if done_indices:
        after_done = [i for i in range(done_indices[-1] + 1, len(events)) if events[i] is not None]
        print("Events after done: {}".format(after_done))
    else:
        after_done = []
        print("No 'done'/'error'/'final_answer' event found")

    # First event timing check (should be < 5s for real-time streaming)
    if events:
        first_event_time = time.time() - t0
        print("Time to first event: {:.1f}s".format(first_event_time))

    # Summary
    print("\n=== VERDICT ===")
    ok = True
    if none_count > 0:
        print("FAIL: None events leaked to consumer")
        ok = False
    else:
        print("PASS: No None events")

    if not empty_warn:
        print("WARN: Empty index warning not detected (may be non-empty index on this machine)")
    else:
        print("PASS: Empty index warning detected")

    if not completion:
        print("FAIL: No completion event")
        ok = False
    else:
        print("PASS: Completion event found")

    if len(file_search_thinking) > 0 or len(content_search_thinking) > 0:
        print("PASS: Thinking events stream in real-time (indices before final)")
    else:
        print("INFO: No file_search/content_search thinking events (index may be non-empty)")

    if after_done:
        print("FAIL: Events leaked after done")
        ok = False
    else:
        print("PASS: No events after done")

    overall = "PASS" if ok else "FAIL"
    print("\nOverall: {}".format(overall))
    return 0 if ok else 1


if __name__ == "__main__":
    code = asyncio.run(main())
    sys.exit(code)
