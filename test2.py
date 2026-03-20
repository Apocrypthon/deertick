import timeit

closes = [float(i) for i in range(100)]
def sum_closes():
    return sum(closes)

def mean_closes():
    import statistics
    return statistics.mean(closes)

print(timeit.timeit("sum_closes()", globals=globals(), number=100000))
print(timeit.timeit("mean_closes()", globals=globals(), number=100000))
