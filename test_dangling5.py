import timeit

class AIMessage:
    type = "ai"
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls

class ToolMessage:
    type = "tool"
    def __init__(self, tool_call_id):
        self.tool_call_id = tool_call_id

class HumanMessage:
    type = "human"

messages = []
for i in range(100):
    messages.append(HumanMessage())
    messages.append(AIMessage([{"id": f"tc_{i}"}]))
    messages.append(ToolMessage(f"tc_{i}"))


def original():
    existing_tool_msg_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, ToolMessage):
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

def optimized():
    existing_tool_msg_ids: set[str] = {msg.tool_call_id for msg in messages if isinstance(msg, ToolMessage)}

    missing_tc_ids: set[str] = set()
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai":
            for tc in getattr(msg, "tool_calls", None) or []:
                tc_id = tc.get("id")
                if tc_id and tc_id not in existing_tool_msg_ids:
                    missing_tc_ids.add(tc_id)
            if missing_tc_ids: break

    if not missing_tc_ids:
        return None


print(timeit.timeit(original, number=10000))
print(timeit.timeit(optimized, number=10000))
