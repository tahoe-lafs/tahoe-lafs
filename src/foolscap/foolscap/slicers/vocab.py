# -*- test-case-name: foolscap.test.test_banana -*-

from twisted.internet.defer import Deferred
from foolscap.constraint import Any, ByteStringConstraint
from foolscap.tokens import Violation, BananaError, INT, STRING
from foolscap.slicer import BaseSlicer, BaseUnslicer, LeafUnslicer
from foolscap.slicer import BananaUnslicerRegistry

class ReplaceVocabularyTable:
    pass

class AddToVocabularyTable:
    pass

class ReplaceVocabSlicer(BaseSlicer):
    # this works somewhat like a dictionary
    opentype = ('set-vocab',)
    trackReferences = False

    def slice(self, streamable, banana):
        # we need to implement slice() (instead of merely sliceBody) so we
        # can get control at the beginning and end of serialization. It also
        # gives us access to the Banana protocol object, so we can manipulate
        # their outgoingVocabulary table.
        self.streamable = streamable
        self.start(banana)
        for o in self.opentype:
            yield o
        # the vocabDict maps strings to index numbers. The far end needs the
        # opposite mapping, from index numbers to strings. We perform the
        # flip here at the sending end.
        stringToIndex = self.obj
        indexToString = dict([(stringToIndex[s],s) for s in stringToIndex])
        assert len(stringToIndex) == len(indexToString) # catch duplicates
        indices = indexToString.keys()
        indices.sort()
        for index in indices:
            string = indexToString[index]
            yield index
            yield string
        self.finish(banana)

    def start(self, banana):
        # this marks the transition point between the old vocabulary dict and
        # the new one, so now is the time we should empty the dict.
        banana.outgoingVocabTableWasReplaced({})

    def finish(self, banana):
        # now we replace the vocab dict
        banana.outgoingVocabTableWasReplaced(self.obj)

class ReplaceVocabUnslicer(LeafUnslicer):
    """Much like DictUnslicer, but keys must be numbers, and values must be
    strings. This is used to set the entire vocab table at once. To add
    individual tokens, use AddVocabUnslicer by sending an (add-vocab num
    string) sequence."""
    opentype = ('set-vocab',)
    unslicerRegistry = BananaUnslicerRegistry
    maxKeys = None
    valueConstraint = ByteStringConstraint(100)

    def setConstraint(self, constraint):
        if isinstance(constraint, Any):
            return
        assert isinstance(constraint, ByteStringConstraint)
        self.valueConstraint = constraint

    def start(self, count):
        self.d = {}
        self.key = None

    def checkToken(self, typebyte, size):
        if self.maxKeys is not None and len(self.d) >= self.maxKeys:
            raise Violation("the table is full")
        if self.key is None:
            if typebyte != INT:
                raise BananaError("VocabUnslicer only accepts INT keys")
        else:
            if typebyte != STRING:
                raise BananaError("VocabUnslicer only accepts STRING values")
            if self.valueConstraint:
                self.valueConstraint.checkToken(typebyte, size)

    def receiveChild(self, token, ready_deferred=None):
        assert not isinstance(token, Deferred)
        assert ready_deferred is None
        if self.key is None:
            if self.d.has_key(token):
                raise BananaError("duplicate key '%s'" % token)
            self.key = token
        else:
            self.d[self.key] = token
            self.key = None

    def receiveClose(self):
        if self.key is not None:
            raise BananaError("sequence ended early: got key but not value")
        # now is the time we replace our protocol's vocab table
        self.protocol.replaceIncomingVocabulary(self.d)
        return ReplaceVocabularyTable, None

    def describe(self):
        if self.key is not None:
            return "<vocabdict>[%s]" % self.key
        else:
            return "<vocabdict>"


class AddVocabSlicer(BaseSlicer):
    opentype = ('add-vocab',)
    trackReferences = False

    def __init__(self, value):
        assert isinstance(value, str)
        self.value = value

    def slice(self, streamable, banana):
        # we need to implement slice() (instead of merely sliceBody) so we
        # can get control at the beginning and end of serialization. It also
        # gives us access to the Banana protocol object, so we can manipulate
        # their outgoingVocabulary table.
        self.streamable = streamable
        self.start(banana)
        for o in self.opentype:
            yield o
        yield self.index
        yield self.value
        self.finish(banana)

    def start(self, banana):
        # this marks the transition point between the old vocabulary dict and
        # the new one, so now is the time we should decide upon the key. It
        # is important that we *do not* add it to the dict yet, otherwise
        # we'll send (add-vocab NN [VOCAB#NN]), which is kind of pointless.
        index = banana.allocateEntryInOutgoingVocabTable(self.value)
        self.index = index

    def finish(self, banana):
        banana.outgoingVocabTableWasAmended(self.index, self.value)

class AddVocabUnslicer(BaseUnslicer):
    # (add-vocab num string): self.vocab[num] = string
    opentype = ('add-vocab',)
    unslicerRegistry = BananaUnslicerRegistry
    index = None
    value = None
    valueConstraint = ByteStringConstraint(100)

    def setConstraint(self, constraint):
        if isinstance(constraint, Any):
            return
        assert isinstance(constraint, ByteStringConstraint)
        self.valueConstraint = constraint

    def checkToken(self, typebyte, size):
        if self.index is None:
            if typebyte != INT:
                raise BananaError("Vocab key must be an INT")
        elif self.value is None:
            if typebyte != STRING:
                raise BananaError("Vocab value must be a STRING")
            if self.valueConstraint:
                self.valueConstraint.checkToken(typebyte, size)
        else:
            raise Violation("add-vocab only accepts two values")

    def receiveChild(self, obj, ready_deferred=None):
        assert not isinstance(obj, Deferred)
        assert ready_deferred is None
        if self.index is None:
            self.index = obj
        else:
            self.value = obj

    def receiveClose(self):
        if self.index is None or self.value is None:
            raise BananaError("sequence ended too early")
        self.protocol.addIncomingVocabulary(self.index, self.value)
        return AddToVocabularyTable, None

    def describe(self):
        if self.index is not None:
            return "<add-vocab>[%d]" % self.index
        return "<add-vocab>"
