messages = [
    type("HumanMessage", (), {"type": "human"}),
    type("AIMessage", (), {"type": "ai"}),
    type("ToolMessage", (), {"type": "tool"}),
] * 10

def original():
    user_messages = [m for m in messages if getattr(m, "type", None) == "human"]
    assistant_messages = [m for m in messages if getattr(m, "type", None) == "ai"]
    return bool(user_messages and assistant_messages)

def optimized():
    has_user = any(getattr(m, "type", None) == "human" for m in messages)
    if not has_user: return False
    return any(getattr(m, "type", None) == "ai" for m in messages)

import timeit
print(timeit.timeit(original, number=100000))
print(timeit.timeit(optimized, number=100000))
