messages = [
    type("HumanMessage", (), {"type": "human"}),
    type("AIMessage", (), {"type": "ai"}),
    type("ToolMessage", (), {"type": "tool"}),
]

import timeit
print(timeit.timeit("user_messages = [m for m in messages if m.type == 'human']", globals=globals(), number=100000))
print(timeit.timeit("user_messages = sum(1 for m in messages if m.type == 'human')", globals=globals(), number=100000))
