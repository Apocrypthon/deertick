messages = [
    type("HumanMessage", (), {"type": "human"}),
    type("AIMessage", (), {"type": "ai"}),
    type("ToolMessage", (), {"type": "tool"}),
] * 10

def original():
    user_messages = [m for m in messages if m.type == "human"]
    assistant_messages = [m for m in messages if m.type == "ai"]
    return len(user_messages) == 1 and len(assistant_messages) >= 1

def optimized():
    user_count = sum(1 for m in messages if m.type == "human")
    if user_count != 1:
        return False
    return any(m.type == "ai" for m in messages)

import timeit
print(timeit.timeit(original, number=100000))
print(timeit.timeit(optimized, number=100000))
