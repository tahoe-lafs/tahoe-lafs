
import sha

# here is the list of initial vocab tables. If the two ends negotiate to use
# initial-vocab-table-index N, then both sides will start with the words from
# INITIAL_VOCAB_TABLES[n] for their VOCABized tokens.

vocab_v0 = []
vocab_v1 = [ # all opentypes used in 0.0.6
    "none", "boolean", "reference",
    "dict", "list", "tuple", "set", "immutable-set",
    "unicode", "set-vocab", "add-vocab",
    "call", "arguments", "answer", "error",
    "my-reference", "your-reference", "their-reference", "copyable",
    # these are only used by storage.py
    "instance", "module", "class", "method", "function",
    # I'm not sure this one is actually used anywhere, but the first 127 of
    # these are basically free.
    "attrdict",
    ]
INITIAL_VOCAB_TABLES = { 0: vocab_v0, 1: vocab_v1 }

# to insure both sides agree on the actual words, we can hash the vocab table
# into a short string. This is included in the negotiation decision and
# compared by the receiving side.

def hashVocabTable(table_index):
    data = "\x00".join(INITIAL_VOCAB_TABLES[table_index])
    digest = sha.new(data).hexdigest()
    return digest[:4]

def getVocabRange():
    keys = INITIAL_VOCAB_TABLES.keys()
    return min(keys), max(keys)
