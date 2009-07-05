

def netstring(s):
    assert isinstance(s, str), s # no unicode here
    return "%d:%s," % (len(s), s,)

def split_netstring(data, numstrings,
                    position=0,
                    required_trailer=None):
    """like string.split(), but extracts netstrings. Ignore all bytes of data
    before the 'position' byte. Return a tuple of (list of elements (numstrings
    in length), new position index). The new position index points to the first
    byte which was not consumed (the 'required_trailer', if any, counts as
    consumed).  If 'required_trailer' is not None, throw ValueError if leftover
    data does not exactly equal 'required_trailer'."""

    assert type(position) in (int, long), (repr(position), type(position))
    elements = []
    assert numstrings >= 0
    while position < len(data):
        colon = data.index(":", position)
        length = int(data[position:colon])
        string = data[colon+1:colon+1+length]
        assert len(string) == length, (len(string), length)
        elements.append(string)
        position = colon+1+length
        assert data[position] == ",", position
        position += 1
        if len(elements) == numstrings:
            break
    if len(elements) < numstrings:
        raise ValueError("ran out of netstrings")
    if required_trailer is not None:
        if ((len(data) - position) != len(required_trailer)) or (data[position:] != required_trailer):
            raise ValueError("leftover data in netstrings")
        return (elements, position + len(required_trailer))
    else:
        return (elements, position)
