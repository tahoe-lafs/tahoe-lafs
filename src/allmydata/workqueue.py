
import os, shutil
from zope.interface import Interface, implements
from allmydata.util import bencode

class IWorkQueue(Interface):
    """Each filetable root is associated a work queue, which is persisted on
    disk and contains idempotent actions that need to be performed. After
    each action is completed, it is removed from the queue.

    The queue is broken up into several sections. First are the 'upload'
    steps. After this are the 'add_subpath' commands. The last section has
    the 'unlink' steps. Somewhere in here are the 'retain' steps.. maybe
    interspersed with 'upload', maybe after 'add_subpath' and before
    'unlink'.

    The general idea is that the processing of the work queue could be
    interrupted at any time, in the middle of a step, and the next time the
    application is started, the step can be re-started without problems. The
    placement of the 'retain' commands depends upon how long we might expect
    the app to be offline.
    """

    def create_tempfile():
        """Return (f, filename)."""
    def create_boxname():
        """Return a unique box name (as a string)."""

    def add_upload_chk(source_filename, stash_uri_in_boxname):
        """This step uploads a file to the mesh and obtains a content-based
        URI which can be used to later retrieve the same contents ('CHK'
        mode). This URI includes unlink rights. It does not mark the file for
        retention.

        When the upload is complete, the resulting URI is stashed in a 'box'
        with the specified name. This is basically a local variable. A later
        'add_subpath' step will reference this boxname and retrieve the URI.
        """

    def add_upload_ssk(source_filename, write_capability, previous_version):
        """This step uploads a file to the mesh in a way that replaces the
        previous version and does not require a change to the ID referenced
        by the parent.
        """

    def add_retain_ssk(read_capability):
        """Arrange for the given SSK to be kept alive."""

    def add_unlink_ssk(write_capability):
        """Stop keeping the given SSK alive."""

    def add_retain_uri_from_box(boxname):
        """When executed, this step retrieves the URI from the given box and
        marks it for retention: this adds it to a list of all URIs that this
        system cares about, which will initiate filechecking/repair for the
        file."""

    def add_addpath(boxname, path):
        """When executed, this step will retrieve the URI from the given box
        and call root.add(path, URIishthingyTODO, etc).
        """

    def add_unlink_uri(uri):
        """When executed, this step will unlink the data referenced by the
        given URI: the unlink rights are used to tell any shareholders to
        unlink the file (possibly deleting it), and the URI is removed from
        the list that this system cares about, cancelling filechecking/repair
        for the file.

        All 'unlink' steps are pushed to the end of the queue.
        """

    def add_delete_tempfile(filename):
        """This step will delete a tempfile created by create_tempfile."""

    def add_delete_box(boxname):
        """When executed, this step deletes the given box."""


class Step(object):
    def setup(self, stepname, basedir):
        self.basedir = basedir
        self.stepname = stepname
        self.stepbase = os.path.join(self.basedir, self.stepname)

    def remove(self, _ignored=None):
        trashdir = os.path.join(self.basedir, "trash", self.stepname)
        os.rename(self.stepbase, trashdir)
        shutil.rmtree(trashdir)

class UploadSSKStep(Step):
    def start(self):
        f = open(os.path.join(self.stepbase, "source_filename"), "r")
        source_filename = f.read()
        f.close()
        f = open(os.path.join(self.stepbase, "write_capability"), "r")
        write_cap = bencode.bdecode(f.read())
        f.close()
        f = open(os.path.join(self.stepbase, "previous_version"), "r")
        previous_version = bencode.bdecode(f.read())
        f.close()

        n = SSKNode()
        n.set_version(previous_version)
        n.set_write_capability(write_cap)
        f = open(source_filename, "rb")
        data = f.read()
        f.close()
        published_data = n.write_new_version(data)
        d = self.push_ssk(n.ssk_index, n.vresion, published_data)
        d.addCallback(self.remove)
        return d


class WorkQueue(object):
    implements(IWorkQueue)
    def __init__(self, basedir):
        self.basedir = basedir
    # methods to add entries to the queue

    # methods to perform work

    def get_next_step(self):
        stepname = self._find_first_step()
        stepbase = os.path.join(self.basedir, stepname)
        f = open(os.path.join(stepbase, "type"), "r")
        stype = f.read().strip()
        f.close()
        if stype == "upload_ssk":
            s = UploadSSKStep()
        # ...
        else:
            raise RuntimeError("unknown step type '%s'" % stype)
        s.setup(stepname, self.basedir)
        d = s.start()
        return d




AES_KEY_LENGTH = 16
def make_aes_key():
    return os.urandom(16)
def make_rsa_key():
    raise NotImplementedError

class MutableSSKTracker(object):
    """I represent a mutable file, indexed by an SSK.
    """

    def create(self):
        # if you create the node this way, you will have both read and write
        # capabilities
        self.priv_key, self.pub_key = make_rsa_key()
        self.ssk_index = sha(self.pub_key.serialized())
        self.write_key = make_aes_key()
        self.read_key = sha(self.write_key)[:AES_KEY_LENGTH]
        self.version = 0

    def set_version(self, version):
        self.version = version

    def set_read_capability(self, read_cap):
        (self.ssk_index, self.read_key) = read_cap

    def set_write_capability(self, write_cap):
        # TODO: add some assertions here, if someone calls both
        # set_read_capability and set_write_capability, make sure the keys
        # match
        (self.ssk_index, self.write_key) = write_cap
        self.read_key = sha(self.write_key)[:AES_KEY_LENGTH]

    def extract_readwrite_from_published(self, published_data, write_key):
        self.write_key = write_key
        self.read_key = sha(self.write_key)[:AES_KEY_LENGTH]
        self._extract(published_data)
        self.priv_key = aes_decrypt(write_key, self.encrypted_privkey)
        assert self.priv_key.is_this_your_pub_key(self.pub_key)

    def extract_readonly_from_published(self, published_data, read_key):
        self.write_key = None
        self.read_key = read_key
        self._extract(published_data)
        self.priv_key = None

    def _extract(self, published_data):
        (signed_data, serialized_pub_key, sig) = unserialize(published_data)
        self.pub_key = unserialize(serialized_pub_key)
        self.pub_key.check_signature(sig, signed_data)
        (encrypted_privkey, encrypted_data, version) = unserialize(signed_data)
        self.data = aes_decrypt(self.read_key, encrypted_data)
        self.encrypted_privkey = encrypted_privkey

    def get_read_capability(self):
        return (self.ssk_index, self.read_key)

    def get_write_capability(self):
        if not self.write_key:
            raise NotCapableError("This SSKNode is read-only")
        return (self.ssk_index, self.write_key)

    def write_new_version(self, data):
        if not self.write_key:
            raise NotCapableError("This SSKNode is read-only")
        encrypted_privkey = aes_encrypt(self.write_key,
                                        self.priv_key.serialized())
        encrypted_data = aes_encrypt(self.read_key, data)
        self.version += 1
        signed_data = serialize((encrypted_privkey,
                                 encrypted_data,
                                 self.version))
        sig = self.priv_key.sign(signed_data)
        serialized_pub_key = self.pub_key.serialized()
        published_data = serialize((signed_data, serialized_pub_key, sig))
        return published_data

def make_new_SSK_node():
    n = SSKNode()
    n.create()
    return n

def extract_readwrite_SSK_node(published_data, write_key):
    n = SSKNode()
    n.extract_readwrite_SSK_node(published_data, write_key)
    return n

def extract_readonly_SSK_node(published_data, read_key):
    n = SSKNode()
    n.extract_readonly_from_published(published_data, read_key)
    return n

