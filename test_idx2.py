import timeit

messages = list(range(100))
msg = 90

def original():
    try:
        idx = messages.index(msg)
    except ValueError:
        return False
    return True

def optimized():
    idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i] == msg:
            idx = i
            break
    if idx == -1: return False
    return True

print(timeit.timeit(original, number=100000))
print(timeit.timeit(optimized, number=100000))
