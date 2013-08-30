
def map(f, xs, ys=None):
    if ys is None:
        return [f(x) for x in xs]
    else:
        if len(xs) != len(ys):
            raise AssertionError("iterators must be the same length")
        return [f(x, y) for (x, y) in zip(xs, ys)]