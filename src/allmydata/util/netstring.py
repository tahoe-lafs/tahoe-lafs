

def netstring(s):
    assert isinstance(s, str), s # no unicode here
    return "%d:%s," % (len(s), s,)

def split_netstring(data, numstrings,
                    allow_leftover=False,
                    required_trailer=""):
    """like string.split(), but extracts netstrings. If allow_leftover=False,
    I return numstrings elements, and throw ValueError if there was leftover
    data that does not exactly equal 'required_trailer'. If
    allow_leftover=True, required_trailer must be empty, and I return
    numstrings+1 elements, in which the last element is the leftover data
    (possibly an empty string)"""

    assert not (allow_leftover and required_trailer)

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
    if data != required_trailer:
        raise ValueError("leftover data in netstrings")
    return tuple(elements)

