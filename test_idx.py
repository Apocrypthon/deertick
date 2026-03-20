import timeit

messages = list(range(100))
msg = 90

def with_index():
    try:
        idx = messages.index(msg)
    except ValueError:
        return False
    return True

def with_rev_enumerate():
    idx = -1
    for i, m in enumerate(reversed(messages)):
        if m == msg:
            idx = len(messages) - 1 - i
            break
    if idx == -1: return False
    return True

print(timeit.timeit(with_index, number=100000))
print(timeit.timeit(with_rev_enumerate, number=100000))
