import timeit
from typing import Any

messages = []
for i in range(100):
    messages.append(type("HumanMessage", (), {"type": "human"}))
    messages.append(type("AIMessage", (), {"type": "ai", "tool_calls": [{"id": f"tc_{i}"}]}))
    messages.append(type("ToolMessage", (), {"type": "tool", "tool_call_id": f"tc_{i}"}))

messages[-2].tool_calls = [{"id": "tc_missing"}]

def original():
    existing_tool_msg_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, type("ToolMessage", (), {"type": "tool"})): # Mock isinstance
            pass
        if getattr(msg, "type", None) == "tool":
            existing_tool_msg_ids.add(msg.tool_call_id)

    needs_patch = False
    for msg in messages:
        if getattr(msg, "type", None) != "ai":
            continue
        for tc in getattr(msg, "tool_calls", None) or []:
            tc_id = tc.get("id")
            if tc_id and tc_id not in existing_tool_msg_ids:
                needs_patch = True
                break
        if needs_patch:
            break

    if not needs_patch:
        return None

    patched: list = []
    patched_ids: set[str] = set()
    patch_count = 0
    for msg in messages:
        patched.append(msg)
        if getattr(msg, "type", None) != "ai":
            continue
        for tc in getattr(msg, "tool_calls", None) or []:
            tc_id = tc.get("id")
            if tc_id and tc_id not in existing_tool_msg_ids and tc_id not in patched_ids:
                patched.append(
                    f"patched {tc_id}"
                )
                patched_ids.add(tc_id)
                patch_count += 1
    return patched

def optimized():
    # Only collect missing patch IDs directly in one pass
    existing_tool_msg_ids: set[str] = {msg.tool_call_id for msg in messages if getattr(msg, "type", None) == "tool"}

    missing_tc_ids: set[str] = set()
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai":
            for tc in getattr(msg, "tool_calls", None) or []:
                tc_id = tc.get("id")
                if tc_id and tc_id not in existing_tool_msg_ids:
                    missing_tc_ids.add(tc_id)
            if missing_tc_ids: break # Usually just look at the last AI message

    if not missing_tc_ids:
        return None

    patched: list = []
    patched_ids: set[str] = set()
    patch_count = 0
    for msg in messages:
        patched.append(msg)
        if getattr(msg, "type", None) == "ai":
            for tc in getattr(msg, "tool_calls", None) or []:
                tc_id = tc.get("id")
                if tc_id and tc_id in missing_tc_ids and tc_id not in patched_ids:
                    patched.append(f"patched {tc_id}")
                    patched_ids.add(tc_id)
                    patch_count += 1
    return patched

print(timeit.timeit(original, number=10000))
print(timeit.timeit(optimized, number=10000))
