
# This provides a base for the various Constraint subclasses to use. Those
# Constraint subclasses live next to the slicers. It also contains
# Constraints for primitive types (int, str).

# This imports foolscap.tokens, but no other Foolscap modules.

import re
from zope.interface import implements, Interface

from foolscap.tokens import Violation, BananaError, SIZE_LIMIT, \
     STRING, LIST, INT, NEG, LONGINT, LONGNEG, VOCAB, FLOAT, OPEN, \
     tokenNames

everythingTaster = {
    # he likes everything
    STRING: SIZE_LIMIT,
    LIST: None,
    INT: None,
    NEG: None,
    LONGINT: SIZE_LIMIT,
    LONGNEG: SIZE_LIMIT,
    VOCAB: None,
    FLOAT: None,
    OPEN: None,
    }
openTaster = {
    OPEN: None,
    }
nothingTaster = {}

class UnboundedSchema(Exception):
    pass

class IConstraint(Interface):
    pass
class IRemoteMethodConstraint(IConstraint):
    def getPositionalArgConstraint(argnum):
        """Return the constraint for posargs[argnum]. This is called on
        inbound methods when receiving positional arguments. This returns a
        tuple of (accept, constraint), where accept=False means the argument
        should be rejected immediately, regardless of what type it might be."""
    def getKeywordArgConstraint(argname, num_posargs=0, previous_kwargs=[]):
        """Return the constraint for kwargs[argname]. The other arguments are
        used to handle mixed positional and keyword arguments. Returns a
        tuple of (accept, constraint)."""

    def checkAllArgs(args, kwargs, inbound):
        """Submit all argument values for checking. When inbound=True, this
        is called after the arguments have been deserialized, but before the
        method is invoked. When inbound=False, this is called just inside
        callRemote(), as soon as the target object (and hence the remote
        method constraint) is located.

        This should either raise Violation or return None."""
        pass
    def getResponseConstraint():
        """Return an IConstraint-providing object to enforce the response
        constraint. This is called on outbound method calls so that when the
        response starts to come back, we can start enforcing the appropriate
        constraint right away."""
    def checkResults(results, inbound):
        """Inspect the results of invoking a method call. inbound=False is
        used on the side that hosts the Referenceable, just after the target
        method has provided a value. inbound=True is used on the
        RemoteReference side, just after it has finished deserializing the
        response.

        This should either raise Violation or return None."""

class Constraint:
    """
    Each __schema__ attribute is turned into an instance of this class, and
    is eventually given to the unserializer (the 'Unslicer') to enforce as
    the tokens are arriving off the wire.
    """

    implements(IConstraint)

    taster = everythingTaster
    """the Taster is a dict that specifies which basic token types are
    accepted. The keys are typebytes like INT and STRING, while the
    values are size limits: the body portion of the token must not be
    longer than LIMIT bytes.
    """

    strictTaster = False
    """If strictTaster is True, taste violations are raised as BananaErrors
    (indicating a protocol error) rather than a mere Violation.
    """

    opentypes = None
    """opentypes is a list of currently acceptable OPEN token types. None
    indicates that all types are accepted. An empty list indicates that no
    OPEN tokens are accepted.
    """

    name = None
    """Used to describe the Constraint in a Violation error message"""

    def checkToken(self, typebyte, size):
        """Check the token type. Raise an exception if it is not accepted
        right now, or if the body-length limit is exceeded."""

        limit = self.taster.get(typebyte, "not in list")
        if limit == "not in list":
            if self.strictTaster:
                raise BananaError("invalid token type")
            else:
                raise Violation("%s token rejected by %s" % \
                                (tokenNames[typebyte], self.name))
        if limit and size > limit:
            raise Violation("token too large: %d>%d" % (size, limit))

    def setNumberTaster(self, maxValue):
        self.taster = {INT: None,
                       NEG: None,
                       LONGINT: None, # TODO
                       LONGNEG: None,
                       FLOAT: None,
                       }
    def checkOpentype(self, opentype):
        """Check the OPEN type (the tuple of Index Tokens). Raise an
        exception if it is not accepted.
        """

        if self.opentypes == None:
            return

        for o in self.opentypes:
            if len(o) == len(opentype):
                if o == opentype:
                    return
            if len(o) > len(opentype):
                # we might have a partial match: they haven't flunked yet
                if opentype == o[:len(opentype)]:
                    return # still in the running
        print "opentype %s, self.opentypes %s" % (opentype, self.opentypes)
        raise Violation, "unacceptable OPEN type '%s'" % (opentype,)

    def checkObject(self, obj, inbound):
        """Validate an existing object. Usually objects are validated as
        their tokens come off the wire, but pre-existing objects may be
        added to containers if a REFERENCE token arrives which points to
        them. The older objects were were validated as they arrived (by a
        different schema), but now they must be re-validated by the new
        schema.

        A more naive form of validation would just accept the entire object
        tree into memory and then run checkObject() on the result. This
        validation is too late: it is vulnerable to both DoS and
        made-you-run-code attacks.

        If inbound=True, this object is arriving over the wire. If
        inbound=False, this is being called to validate an existing object
        before it is sent over the wire. This is done as a courtesy to the
        remote end, and to improve debuggability.

        Most constraints can use the same checker for both inbound and
        outbound objects.
        """
        # this default form passes everything
        return

    def maxSize(self, seen=None):
        """
        I help a caller determine how much memory could be consumed by the
        input stream while my constraint is in effect.

        My constraint will be enforced against the bytes that arrive over
        the wire. Eventually I will either accept the incoming bytes and my
        Unslicer will provide an object to its parent (including any
        subobjects), or I will raise a Violation exception which will kick
        my Unslicer into 'discard' mode.

        I define maxSizeAccept as the maximum number of bytes that will be
        received before the stream is accepted as valid. maxSizeReject is
        the maximum that will be received before a Violation is raised. The
        max of the two provides an upper bound on single objects. For
        container objects, the upper bound is probably (n-1)*accept +
        reject, because there can only be one outstanding
        about-to-be-rejected object at any time.

        I return (maxSizeAccept, maxSizeReject).

        I raise an UnboundedSchema exception if there is no bound.
        """
        raise UnboundedSchema

    def maxDepth(self):
        """I return the greatest number Slicer objects that might exist on
        the SlicerStack (or Unslicers on the UnslicerStack) while processing
        an object which conforms to this constraint. This is effectively the
        maximum depth of the object tree. I raise UnboundedSchema if there is
        no bound.
        """
        raise UnboundedSchema

    COUNTERBYTES = 64 # max size of opencount

    def OPENBYTES(self, dummy):
        # an OPEN,type,CLOSE sequence could consume:
        #  64 (header)
        #  1 (OPEN)
        #   64 (header)
        #   1 (STRING)
        #   1000 (value)
        #    or
        #   64 (header)
        #   1 (VOCAB)
        #  64 (header)
        #  1 (CLOSE)
        # for a total of 65+1065+65 = 1195
        return self.COUNTERBYTES+1 + 64+1+1000 + self.COUNTERBYTES+1

class OpenerConstraint(Constraint):
    taster = openTaster

class Any(Constraint):
    pass # accept everything

# constraints which describe individual banana tokens

class StringConstraint(Constraint):
    opentypes = [] # redundant, as taster doesn't accept OPEN
    name = "StringConstraint"

    def __init__(self, maxLength=1000, minLength=0, regexp=None):
        self.maxLength = maxLength
        self.minLength = minLength
        # regexp can either be a string or a compiled SRE_Match object..
        # re.compile appears to notice SRE_Match objects and pass them
        # through unchanged.
        self.regexp = None
        if regexp:
            self.regexp = re.compile(regexp)
        self.taster = {STRING: self.maxLength,
                       VOCAB: None}
    def checkObject(self, obj, inbound):
        if not isinstance(obj, (str, unicode)):
            raise Violation("not a String")
        if self.maxLength != None and len(obj) > self.maxLength:
            raise Violation("string too long (%d > %d)" %
                            (len(obj), self.maxLength))
        if len(obj) < self.minLength:
            raise Violation("string too short (%d < %d)" %
                            (len(obj), self.minLength))
        if self.regexp:
            if not self.regexp.search(obj):
                raise Violation("regexp failed to match")

    def maxSize(self, seen=None):
        if self.maxLength == None:
            raise UnboundedSchema
        return 64+1+self.maxLength
    def maxDepth(self, seen=None):
        return 1

class IntegerConstraint(Constraint):
    opentypes = [] # redundant
    # taster set in __init__
    name = "IntegerConstraint"

    def __init__(self, maxBytes=-1):
        # -1 means s_int32_t: INT/NEG instead of INT/NEG/LONGINT/LONGNEG
        # None means unlimited
        assert maxBytes == -1 or maxBytes == None or maxBytes >= 4
        self.maxBytes = maxBytes
        self.taster = {INT: None, NEG: None}
        if maxBytes != -1:
            self.taster[LONGINT] = maxBytes
            self.taster[LONGNEG] = maxBytes

    def checkObject(self, obj, inbound):
        if not isinstance(obj, (int, long)):
            raise Violation("not a number")
        if self.maxBytes == -1:
            if obj >= 2**31 or obj < -2**31:
                raise Violation("number too large")
        elif self.maxBytes != None:
            if abs(obj) >= 2**(8*self.maxBytes):
                raise Violation("number too large")

    def maxSize(self, seen=None):
        if self.maxBytes == None:
            raise UnboundedSchema
        if self.maxBytes == -1:
            return 64+1
        return 64+1+self.maxBytes
    def maxDepth(self, seen=None):
        return 1

class NumberConstraint(IntegerConstraint):
    name = "NumberConstraint"

    def __init__(self, maxBytes=1024):
        assert maxBytes != -1  # not valid here
        IntegerConstraint.__init__(self, maxBytes)
        self.taster[FLOAT] = None

    def checkObject(self, obj, inbound):
        if isinstance(obj, float):
            return
        IntegerConstraint.checkObject(self, obj, inbound)

    def maxSize(self, seen=None):
        # floats are packed into 8 bytes, so the shortest FLOAT token is
        # 64+1+8
        intsize = IntegerConstraint.maxSize(self, seen)
        return max(64+1+8, intsize)
    def maxDepth(self, seen=None):
        return 1



#TODO
class Shared(Constraint):
    name = "Shared"

    def __init__(self, constraint, refLimit=None):
        self.constraint = IConstraint(constraint)
        self.refLimit = refLimit
    def maxSize(self, seen=None):
        if not seen: seen = []
        if self in seen:
            raise UnboundedSchema # recursion
        seen.append(self)
        return self.constraint.maxSize(seen)
    def maxDepth(self, seen=None):
        if not seen: seen = []
        if self in seen:
            raise UnboundedSchema # recursion
        seen.append(self)
        return self.constraint.maxDepth(seen)

#TODO: might be better implemented with a .optional flag
class Optional(Constraint):
    name = "Optional"

    def __init__(self, constraint, default):
        self.constraint = IConstraint(constraint)
        self.default = default
    def maxSize(self, seen=None):
        if not seen: seen = []
        if self in seen:
            raise UnboundedSchema # recursion
        seen.append(self)
        return self.constraint.maxSize(seen)
    def maxDepth(self, seen=None):
        if not seen: seen = []
        if self in seen:
            raise UnboundedSchema # recursion
        seen.append(self)
        return self.constraint.maxDepth(seen)
