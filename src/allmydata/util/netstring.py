

def netstring(s):
    assert isinstance(s, str), s # no unicode here
    return "%d:%s," % (len(s), s,)

def split_netstring(data, numstrings, allow_leftover=False):
    """like string.split(), but extracts netstrings. If allow_leftover=False,
    returns numstrings elements, and throws ValueError if there was leftover
    data. If allow_leftover=True, returns numstrings+1 elements, in which the
    last element is the leftover data (possibly an empty string)"""
    elements = []
    assert numstrings >= 0
    while data:
        colon = data.index(":")
        length = int(data[:colon])
        string = data[colon+1:colon+1+length]
        assert len(string) == length
        elements.append(string)
        assert data[colon+1+length] == ","
        data = data[colon+1+length+1:]
        if len(elements) == numstrings:
            break
    if len(elements) < numstrings:
        raise ValueError("ran out of netstrings")
    if allow_leftover:
        return tuple(elements + [data])
    if data:
        raise ValueError("leftover data in netstrings")
    return tuple(elements)

