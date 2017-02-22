
MODE_CHECK = "MODE_CHECK" # query all peers
MODE_ANYTHING = "MODE_ANYTHING" # one recoverable version
MODE_WRITE = "MODE_WRITE" # replace all shares, probably.. not for initial
                          # creation
MODE_READ = "MODE_READ"
MODE_REPAIR = "MODE_REPAIR" # query all peers, get the privkey

class NotWriteableError(Exception):
    pass

class BadShareError(Exception):
    """This represents an error discovered in a particular share, during
    retrieve, from which we can recover by using some other share. This does
    *not* include local coding errors.
    """

class NeedMoreDataError(BadShareError):
    def __init__(self, needed_bytes, encprivkey_offset, encprivkey_length):
        Exception.__init__(self)
        self.needed_bytes = needed_bytes # up through EOF
        self.encprivkey_offset = encprivkey_offset
        self.encprivkey_length = encprivkey_length
    def __repr__(self):
        return "<NeedMoreDataError (%d bytes)>" % self.needed_bytes

class UncoordinatedWriteError(Exception):
    def __repr__(self):
        return ("<%s -- You, oh user, tried to change a file or directory "
                "at the same time as another process was trying to change it. "
                " To avoid data loss, don't do this.  Please see "
                "docs/write_coordination.rst for details.>" %
                (self.__class__.__name__,))

class UnrecoverableFileError(Exception):
    pass

class NotEnoughServersError(Exception):
    """There were not enough functioning servers available to place shares
    upon. This might result from all servers being full or having an error, a
    local bug which causes all server requests to fail in the same way, or
    from there being zero servers. The first error received (if any) is
    stored in my .first_error attribute."""
    def __init__(self, why, first_error=None):
        Exception.__init__(self, why, first_error)
        self.first_error = first_error

class CorruptShareError(BadShareError):
    def __init__(self, server, shnum, reason):
        self.args = (server, shnum, reason)
        self.server = server
        self.shnum = shnum
        self.reason = reason
    def __str__(self):
        return "<CorruptShareError server=%s shnum[%d]: %s" % \
               (self.server.get_name(), self.shnum, self.reason)

class UnknownVersionError(BadShareError):
    """The share we received was of a version we don't recognize."""
