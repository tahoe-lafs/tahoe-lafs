
import os, shutil, sha
from zope.interface import Interface, implements
from twisted.internet import defer
from allmydata.util import bencode
from allmydata.util.idlib import b2a
from allmydata.Crypto.Cipher import AES

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

    def create_tempfile(suffix=""):
        """Return (f, filename)."""
    def create_boxname(contents=None):
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

class NotCapableError(Exception):
    """You have tried to write to a read-only node."""


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

        n = MutableSSKTracker()
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
        assert basedir.endswith("workqueue")
        self.basedir = basedir
        self.seqnum = 0
        self.tmpdir = os.path.join(basedir, "tmp")
        #self.trashdir = os.path.join(basedir, "trash")
        self.filesdir = os.path.join(basedir, "files")
        self.boxesdir = os.path.join(basedir, "boxes")
        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)
        os.makedirs(self.tmpdir)
        #if os.path.exists(self.trashdir):
        #    shutil.rmtree(self.trashdir)
        #os.makedirs(self.trashdir)
        if not os.path.exists(self.filesdir):
            # filesdir is *not* cleared
            os.makedirs(self.filesdir)
        if not os.path.exists(self.boxesdir):
            # likewise, boxesdir is not cleared
            os.makedirs(self.boxesdir)
        # all Steps are recorded in separate files in our basedir. All such
        # files are named with the pattern 'step-END-NNN', where END is
        # either 'first' or 'last'. These steps are to be executed in
        # alphabetical order, with all 'step-first-NNN' steps running before
        # any 'step-last-NNN'.
        for n in os.listdir(self.basedir):
            if n.startswith("step-first-"):
                sn = int(n[len("step-first-"):])
                self.seqnum = max(self.seqnum, sn)
            elif n.startswith("step-last-"):
                sn = int(n[len("step-last-"):])
                self.seqnum = max(self.seqnum, sn)
        # each of these files contains one string per line, and the first
        # line specifies what kind of step it is
        assert self.seqnum < 1000 # TODO: don't let this grow unboundedly

    def set_vdrive(self, vdrive):
        self.vdrive = vdrive

    def create_tempfile(self, suffix=""):
        randomname = b2a(os.urandom(10))
        filename = randomname + suffix
        f = open(os.path.join(self.filesdir, filename), "wb")
        return (f, filename)

    def create_boxname(self):
        return b2a(os.urandom(10))
    def write_to_box(self, boxname, data):
        f = open(os.path.join(self.boxesdir, boxname), "w")
        f.write(data)
        f.flush()
        os.fsync(f)
        f.close()
    def read_from_box(self, boxname):
        f = open(os.path.join(self.boxesdir, boxname), "r")
        data = f.read()
        f.close()
        return data

    def _create_step(self, end, lines):
        assert end in ("first", "last")
        filename = "step-%s-%d" % (end, self.seqnum)
        self.seqnum += 1
        f = open(os.path.join(self.tmpdir, filename), "w")
        for line in lines:
            assert "\n" not in line, line
            f.write(line)
            f.write("\n")
        f.flush()
        os.fsync(f)
        f.close()
        fromfile = os.path.join(self.tmpdir, filename)
        tofile = os.path.join(self.basedir, filename)
        os.rename(fromfile, tofile)

    def _create_step_first(self, lines):
        self._create_step("first", lines)
    def _create_step_last(self, lines):
        self._create_step("last", lines)

    # methods to add entries to the queue
    def add_upload_chk(self, source_filename, stash_uri_in_boxname):
        # source_filename is absolute, and can point to things outside our
        # workqueue.
        lines = ["upload_chk", source_filename, stash_uri_in_boxname]
        self._create_step_first(lines)

    def add_upload_ssk(self, source_filename, write_capability,
                       previous_version):
        lines = ["upload_ssk", source_filename,
                 b2a(write_capability.index), b2a(write_capability.key),
                 str(previous_version)]
        self._create_step_first(lines)

    def add_retain_ssk(self, read_capability):
        lines = ["retain_ssk", b2a(read_capability.index),
                 b2a(read_capability.key)]
        self._create_step_first(lines)

    def add_unlink_ssk(self, write_capability):
        lines = ["unlink_ssk", b2a(write_capability.index),
                 b2a(write_capability.key)]
        self._create_step_last(lines)

    def add_retain_uri_from_box(self, boxname):
        lines = ["retain_uri_from_box", boxname]
        self._create_step_first(lines)

    def add_addpath(self, boxname, path):
        assert isinstance(path, (list, tuple))
        lines = ["addpath", boxname]
        lines.extend(path)
        self._create_step_first(lines)

    def add_unlink_uri(self, uri):
        lines = ["unlink_uri", uri]
        self._create_step_last(lines)

    def add_delete_tempfile(self, filename):
        lines = ["delete_tempfile", filename]
        self._create_step_first(lines)

    def add_delete_box(self, boxname):
        lines = ["delete_box", boxname]
        self._create_step_first(lines)


    # methods to perform work

    def run_next_step(self):
        """Run the next pending step.

        Returns None if there is no next step to run, or a Deferred that
        will fire when the step completes. The step will be removed
        from the queue when it completes."""
        next_step = self.get_next_step()
        if next_step:
            stepname, steptype, lines = self.get_next_step()
            d = self.dispatch_step(steptype, lines)
            d.addCallback(self._delete_step, stepname)
            return d
        # no steps pending, it is safe to clean out leftover files
        self._clean_leftover_files()
        return None

    def _clean_leftover_files(self):
        # there are no steps pending, therefore any leftover files in our
        # filesdir are orphaned and can be deleted. This catches things like
        # a tempfile being created but the application gets interrupted
        # before the upload step which references it gets created, or if an
        # upload step gets written but the remaining sequence (addpath,
        # delete_box) does not.
        for n in os.listdir(self.filesdir):
            os.unlink(os.path.join(self.filesdir, n))
        for n in os.listdir(self.boxesdir):
            os.unlink(os.path.join(self.boxesdir, n))

    def get_next_step(self):
        stepnames = [n for n in os.listdir(self.basedir)
                     if n.startswith("step-")]
        stepnames.sort()
        if not stepnames:
            return None
        stepname = stepnames[0]
        return self._get_step(stepname)

    def _get_step(self, stepname):
        f = open(os.path.join(self.basedir, stepname), "r")
        lines = f.read().split("\n")
        f.close()
        assert lines[-1] == "" # files should end with a newline
        lines.pop(-1) # remove the newline
        steptype = lines.pop(0)
        return stepname, steptype, lines

    def dispatch_step(self, steptype, lines):
        handlername = "step_" + steptype
        if not hasattr(self, handlername):
            raise RuntimeError("unknown workqueue step type '%s'" % steptype)
        handler = getattr(self, handlername)
        d = defer.maybeDeferred(handler, *lines[1:])
        return d

    def _delete_step(self, res, stepname):
        os.unlink(os.path.join(self.basedir, stepname))
        return res

    # debug/test methods
    def count_pending_steps(self):
        return len([n for n in os.listdir(self.basedir)
                    if n.startswith("step-")])
    def get_all_steps(self):
        # returns a list of (steptype, lines) for all steps
        stepnames = []
        for stepname in os.listdir(self.basedir):
            if stepname.startswith("step-"):
                stepnames.append(stepname)
        stepnames.sort()
        steps = []
        for stepname in stepnames:
            steps.append(self._get_step(stepname)[1:])
        return steps
    def run_all_steps(self, ignored=None):
        d = self.run_next_step()
        if d:
            d.addCallback(self.run_all_steps)
            return d
        return defer.succeed(None)


    def open_tempfile(self, filename):
        f = open(os.path.join(self.filesdir, filename), "rb")
        return f

    # work is dispatched to these methods. To add a new step type, add a
    # dispatch method here and an add_ method above.


    def step_upload_chk(self, source_filename, index_a, write_key_a):
        pass
    def step_upload_ssk(self, source_filename, index_a, write_key_a, prev_ver):
        pass

    def step_addpath(self, boxname, *path):
        data = self.read_from_box(boxname)
        child_node = unserialize(data) # TODO: unserialize ?
        return self.vdrive.add(path, child_node)

    def step_retain_ssk(self, index_a, read_key_a):
        pass
    def step_unlink_ssk(self, index_a, write_key_a):
        pass
    def step_retain_uri_from_box(self, boxname):
        pass
    def step_unlink_uri(self, uri):
        pass

    def step_delete_tempfile(self, filename):
        os.unlink(os.path.join(self.filesdir, filename))
    def step_delete_box(self, boxname):
        os.unlink(os.path.join(self.boxesdir, boxname))




AES_KEY_LENGTH = 16
def make_aes_key():
    return os.urandom(16)
def make_rsa_key():
    raise NotImplementedError
def hash_sha(data):
    return sha.new(data).digest()
def hash_sha_to_key(data):
    return sha.new(data).digest()[:AES_KEY_LENGTH]
def aes_encrypt(key, plaintext):
    assert isinstance(key, str)
    assert len(key) == AES_KEY_LENGTH
    cryptor = AES.new(key=key, mode=AES.MODE_CTR, counterstart="\x00"*16)
    crypttext = cryptor.encrypt(plaintext)
    return crypttext
def aes_decrypt(key, crypttext):
    assert isinstance(key, str)
    assert len(key) == AES_KEY_LENGTH
    cryptor = AES.new(key=key, mode=AES.MODE_CTR, counterstart="\x00"*16)
    plaintext = cryptor.decrypt(crypttext)
    return plaintext
def serialize(objects):
    return bencode.bencode(objects)
def unserialize(data):
    return bencode.bdecode(data)

class MutableSSKTracker(object):
    """I represent a mutable file, indexed by an SSK.
    """

    def create(self):
        # if you create the node this way, you will have both read and write
        # capabilities
        self.priv_key, self.pub_key = make_rsa_key()
        self.ssk_index = hash_sha(self.pub_key.serialized())
        self.write_key = make_aes_key()
        self.read_key = hash_sha_to_key(self.write_key)
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
        self.read_key = hash_sha_to_key(self.write_key)

    def extract_readwrite_from_published(self, published_data, write_key):
        self.write_key = write_key
        self.read_key = hash_sha_to_key(self.write_key)
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
            raise NotCapableError("This MutableSSKTracker is read-only")
        return (self.ssk_index, self.write_key)

    def write_new_version(self, data):
        if not self.write_key:
            raise NotCapableError("This MutableSSKTracker is read-only")
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
    n = MutableSSKTracker()
    n.create()
    return n

def extract_readwrite_SSK_node(published_data, write_key):
    n = MutableSSKTracker()
    n.extract_readwrite_SSK_node(published_data, write_key)
    return n

def extract_readonly_SSK_node(published_data, read_key):
    n = MutableSSKTracker()
    n.extract_readonly_from_published(published_data, read_key)
    return n

