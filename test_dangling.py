import timeit

messages = []
for i in range(10):
    messages.append(type("HumanMessage", (), {"type": "human"}))
    messages.append(type("AIMessage", (), {"type": "ai", "tool_calls": [{"id": f"tc_{i}"}]}))
    messages.append(type("ToolMessage", (), {"type": "tool", "tool_call_id": f"tc_{i}"}))

messages[-2].tool_calls = [{"id": "tc_missing"}]

def original():
    existing_tool_msg_ids = set()
    for msg in messages:
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

def optimized():
    # Only one pass to find if we need patching and collect ids
    existing_tool_msg_ids = {msg.tool_call_id for msg in messages if getattr(msg, "type", None) == "tool"}

    needs_patch = False
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai":
            for tc in getattr(msg, "tool_calls", None) or []:
                tc_id = tc.get("id")
                if tc_id and tc_id not in existing_tool_msg_ids:
                    needs_patch = True
                    break
            if needs_patch: break

print(timeit.timeit(original, number=100000))
print(timeit.timeit(optimized, number=100000))
