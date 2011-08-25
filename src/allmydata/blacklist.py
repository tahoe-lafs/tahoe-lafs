
import os

from zope.interface import implements
from twisted.internet import defer
from twisted.python import log as twisted_log

from allmydata.interfaces import IFileNode, IFilesystemNode
from allmydata.util import base32
from allmydata.util.encodingutil import quote_output


class FileProhibited(Exception):
    """This client has been configured to prohibit access to this object."""
    def __init__(self, reason):
        Exception.__init__(self, "Access Prohibited: %s" % quote_output(reason, encoding='utf-8', quotemarks=False))
        self.reason = reason


class Blacklist:
    def __init__(self, blacklist_fn):
        self.blacklist_fn = blacklist_fn
        self.last_mtime = None
        self.entries = {}
        self.read_blacklist() # sets .last_mtime and .entries

    def read_blacklist(self):
        try:
            current_mtime = os.stat(self.blacklist_fn).st_mtime
        except EnvironmentError:
            # unreadable blacklist file means no blacklist
            self.entries.clear()
            return
        try:
            if self.last_mtime is None or current_mtime > self.last_mtime:
                self.entries.clear()
                for line in open(self.blacklist_fn, "r").readlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    si_s, reason = line.split(None, 1)
                    si = base32.a2b(si_s) # must be valid base32
                    self.entries[si] = reason
                self.last_mtime = current_mtime
        except Exception, e:
            twisted_log.err(e, "unparseable blacklist file")
            raise

    def check_storageindex(self, si):
        self.read_blacklist()
        reason = self.entries.get(si, None)
        if reason is not None:
            # log this to logs/twistd.log, since web logs go there too
            twisted_log.msg("blacklist prohibited access to SI %s: %s" %
                            (base32.b2a(si), reason))
        return reason


class ProhibitedNode:
    implements(IFileNode)

    def __init__(self, wrapped_node, reason):
        assert IFilesystemNode.providedBy(wrapped_node), wrapped_node
        self.wrapped_node = wrapped_node
        self.reason = reason

    def get_cap(self):
        return self.wrapped_node.get_cap()

    def get_readcap(self):
        return self.wrapped_node.get_readcap()

    def is_readonly(self):
        return self.wrapped_node.is_readonly()

    def is_mutable(self):
        return self.wrapped_node.is_mutable()

    def is_unknown(self):
        return self.wrapped_node.is_unknown()

    def is_allowed_in_immutable_directory(self):
        return self.wrapped_node.is_allowed_in_immutable_directory()

    def is_alleged_immutable(self):
        return self.wrapped_node.is_alleged_immutable()

    def raise_error(self):
        # We don't raise an exception here because that would prevent the node from being listed.
        pass

    def get_uri(self):
        return self.wrapped_node.get_uri()

    def get_write_uri(self):
        return self.wrapped_node.get_write_uri()

    def get_readonly_uri(self):
        return self.wrapped_node.get_readonly_uri()

    def get_storage_index(self):
        return self.wrapped_node.get_storage_index()

    def get_verify_cap(self):
        return self.wrapped_node.get_verify_cap()

    def get_repair_cap(self):
        return self.wrapped_node.get_repair_cap()

    def get_size(self):
        return None

    def get_current_size(self):
        return defer.succeed(None)

    def get_size_of_best_version(self):
        return defer.succeed(None)

    def check(self, monitor, verify, add_lease):
        return defer.succeed(None)

    def check_and_repair(self, monitor, verify, add_lease):
        return defer.succeed(None)

    def get_version(self):
        return None

    # Omitting any of these methods would fail safe; they are just to ensure correct error reporting.

    def get_best_readable_version(self):
        raise FileProhibited(self.reason)

    def download_best_version(self):
        raise FileProhibited(self.reason)

    def get_best_mutable_version(self):
        raise FileProhibited(self.reason)

    def overwrite(self, new_contents):
        raise FileProhibited(self.reason)

    def modify(self, modifier_cb):
        raise FileProhibited(self.reason)

    def get_servermap(self, mode):
        raise FileProhibited(self.reason)

    def download_version(self, servermap, version):
        raise FileProhibited(self.reason)

    def upload(self, new_contents, servermap):
        raise FileProhibited(self.reason)

    def get_writekey(self):
        raise FileProhibited(self.reason)

    def read(self, consumer, offset=0, size=None):
        raise FileProhibited(self.reason)
