#! /usr/bin/env python
'''
Unit and system tests for tahoe-fuse.
'''

# Note: It's always a SetupFailure, not a TestFailure if a webapi
# operation fails, because this does not indicate a fuse interface
# failure.

# TODO: Unmount after tests regardless of failure or success!

# TODO: Test mismatches between tahoe and fuse/posix.  What about nodes
# with crazy names ('\0', unicode, '/', '..')?  Huuuuge files?
# Huuuuge directories...  As tahoe approaches production quality, it'd
# be nice if the fuse interface did so also by hardening against such cases.

# FIXME: Only create / launch necessary nodes.  Do we still need an introducer and three nodes?

# FIXME: This framework might be replaceable with twisted.trial,
# especially the "layer" design, which is a bit cumbersome when
# using recursion to manage multiple clients.

# FIXME: Identify all race conditions (hint: starting clients, versus
# using the grid fs).

import sys, os, shutil, unittest, subprocess
import tempfile, re, time, random, httplib, urllib
#import traceback

from twisted.python import usage

if sys.platform.startswith('darwin'):
    UNMOUNT_CMD = ['umount']
else:
    # linux, and until we hear otherwise, all other platforms with fuse, by assumption
    UNMOUNT_CMD = ['fusermount', '-u']

# Import fuse implementations:
#FuseDir = os.path.join('.', 'contrib', 'fuse')
#if not os.path.isdir(FuseDir):
#    raise SystemExit('''
#Could not find directory "%s".  Please run this script from the tahoe
#source base directory.
#''' % (FuseDir,))
FuseDir = '.'


### Load each implementation
sys.path.append(os.path.join(FuseDir, 'impl_a'))
import tahoe_fuse as impl_a
sys.path.append(os.path.join(FuseDir, 'impl_b'))
import pyfuse.tahoe as impl_b
sys.path.append(os.path.join(FuseDir, 'impl_c'))
import blackmatch as impl_c

### config info about each impl, including which make sense to run
implementations = {
    'impl_a': dict(module=impl_a,
                   mount_args=['--basedir', '%(nodedir)s', '%(mountpath)s', ],
                   mount_wait=True,
                   suites=['read', ]),
    'impl_b': dict(module=impl_b,
                   todo=True,
                   mount_args=['--basedir', '%(nodedir)s', '%(mountpath)s', ],
                   mount_wait=False,
                   suites=['read', ]),
    'impl_c': dict(module=impl_c,
                   mount_args=['--cache-timeout', '0', '--root-uri', '%(root-uri)s',
                               '--node-directory', '%(nodedir)s', '%(mountpath)s', ],
                   mount_wait=True,
                   suites=['read', 'write', ]),
    'impl_c_no_split': dict(module=impl_c,
                   mount_args=['--cache-timeout', '0', '--root-uri', '%(root-uri)s',
                               '--no-split',
                               '--node-directory', '%(nodedir)s', '%(mountpath)s', ],
                   mount_wait=True,
                   suites=['read', 'write', ]),
    }

if sys.platform == 'darwin':
    del implementations['impl_a']
    del implementations['impl_b']

class FuseTestsOptions(usage.Options):
    optParameters = [
        ["test-type", None, "both",
         "Type of test to run; unit, system or both"
         ],
        ["implementations", None, "all",
         "Comma separated list of implementations to test, or 'all'"
         ],
        ["suites", None, "all",
         "Comma separated list of test suites to run, or 'all'"
         ],
        ["tests", None, None,
         "Comma separated list of specific tests to run"
         ],
        ["path-to-tahoe", None, "../../bin/tahoe",
         "Which 'tahoe' script to use to create test nodes"],
        ["tmp-dir", None, "/tmp",
         "Where the test should create temporary files"],
         # Note; this is '/tmp' because on leopard, tempfile.mkdtemp creates
         # directories in a location which leads paths to exceed what macfuse
         # can handle without leaking un-umount-able fuse processes.
        ]
    optFlags = [
        ["debug-wait", None,
         "Causes the test system to pause at various points, to facilitate debugging"],
        ["web-open", None,
         "Opens a web browser to the web ui at the start of each impl's tests"],
        ["no-cleanup", False,
         "Prevents the cleanup of the working directories, to allow analysis thereof"],
         ]

    def postOptions(self):
        if self['suites'] == 'all':
            self.suites = ['read', 'write']
            # [ ] todo: deduce this from looking for test_ in dir(self)
        else:
            self.suites = map(str.strip, self['suites'].split(','))
        if self['implementations'] == 'all':
            self.implementations = implementations.keys()
        else:
            self.implementations = map(str.strip, self['implementations'].split(','))
        if self['tests']:
            self.tests = map(str.strip, self['tests'].split(','))
        else:
            self.tests = None

### Main flow control:
def main(args):
    config = FuseTestsOptions()
    config.parseOptions(args[1:])

    target = 'all'
    if len(args) > 1:
        target = args.pop(1)

    test_type = config['test-type']
    if test_type not in ('both', 'unit', 'system'):
        raise usage.error('test-type %r not supported' % (test_type,))

    if test_type in ('both', 'unit'):
        run_unit_tests([args[0]])

    if test_type in ('both', 'system'):
        return run_system_test(config)


def run_unit_tests(argv):
    print 'Running Unit Tests.'
    try:
        unittest.main(argv=argv)
    except SystemExit, se:
        pass
    print 'Unit Tests complete.\n'


def run_system_test(config):
    return SystemTest(config).run()

def drepr(obj):
    r = repr(obj)
    if len(r) > 200:
        return r[:100] + ' ... ' + r[-100:]
    else:
        return r

### System Testing:
class SystemTest (object):
    def __init__(self, config):
        self.config = config

        # These members represent test state:
        self.cliexec = None
        self.testroot = None

        # This test state is specific to the first client:
        self.port = None
        self.clientbase = None

    ## Top-level flow control:
    # These "*_layer" methods call eachother in a linear fashion, using
    # exception unwinding to do cleanup properly.  Each "layer" invokes
    # a deeper layer, and each layer does its own cleanup upon exit.

    def run(self):
        print '\n*** Setting up system tests.'
        try:
            results = self.init_cli_layer()
            print '\n*** System Tests complete:'
            total_failures = todo_failures = 0
            for result in results:
                impl_name, failures, total = result
                if implementations[impl_name].get('todo'):
                    todo_failures += failures
                else:
                    total_failures += failures
                print 'Implementation %s: %d failed out of %d.' % result           
            if total_failures:
                print '%s total failures, %s todo' % (total_failures, todo_failures)
                return 1
            else:
                return 0
        except SetupFailure, sfail:
            print
            print sfail
            print '\n*** System Tests were not successfully completed.' 
            return 1

    def maybe_wait(self, msg='waiting', or_if_webopen=False):
        if self.config['debug-wait'] or or_if_webopen and self.config['web-open']:
            print msg
            raw_input()

    def maybe_webopen(self, where=None):
        if self.config['web-open']:
            import webbrowser
            url = self.weburl
            if where is not None:
                url += urllib.quote(where)
            webbrowser.open(url)

    def init_cli_layer(self):
        '''This layer finds the appropriate tahoe executable.'''
        #self.cliexec = os.path.join('.', 'bin', 'tahoe')
        self.cliexec = self.config['path-to-tahoe']
        version = self.run_tahoe('--version')
        print 'Using %r with version:\n%s' % (self.cliexec, version.rstrip())

        return self.create_testroot_layer()

    def create_testroot_layer(self):
        print 'Creating test base directory.'
        #self.testroot = tempfile.mkdtemp(prefix='tahoe_fuse_test_')
        #self.testroot = tempfile.mkdtemp(prefix='tahoe_fuse_test_', dir='/tmp/')
        tmpdir = self.config['tmp-dir']
        if tmpdir:
            self.testroot = tempfile.mkdtemp(prefix='tahoe_fuse_test_', dir=tmpdir)
        else:
            self.testroot = tempfile.mkdtemp(prefix='tahoe_fuse_test_')
        try:
            return self.launch_introducer_layer()
        finally:
            if not self.config['no-cleanup']:
                print 'Cleaning up test root directory.'
                try:
                    shutil.rmtree(self.testroot)
                except Exception, e:
                    print 'Exception removing test root directory: %r' % (self.testroot, )
                    print 'Ignoring cleanup exception: %r' % (e,)
            else:
                print 'Leaving test root directory: %r' % (self.testroot, )


    def launch_introducer_layer(self):
        print 'Launching introducer.'
        introbase = os.path.join(self.testroot, 'introducer')

        # NOTE: We assume if tahoe exits with non-zero status, no separate
        # tahoe child process is still running.
        createoutput = self.run_tahoe('create-introducer', '--basedir', introbase)

        self.check_tahoe_output(createoutput, ExpectedCreationOutput, introbase)

        startoutput = self.run_tahoe('start', '--basedir', introbase)
        try:
            self.check_tahoe_output(startoutput, ExpectedStartOutput, introbase)

            return self.launch_clients_layer(introbase)

        finally:
            print 'Stopping introducer node.'
            self.stop_node(introbase)

    TotalClientsNeeded = 3
    def launch_clients_layer(self, introbase, clientnum = 0):
        if clientnum >= self.TotalClientsNeeded:
            self.maybe_wait('waiting (launched clients)')
            ret = self.create_test_dirnode_layer()
            self.maybe_wait('waiting (ran tests)', or_if_webopen=True)
            return ret

        tmpl = 'Launching client %d of %d.'
        print tmpl % (clientnum,
                      self.TotalClientsNeeded)

        base = os.path.join(self.testroot, 'client_%d' % (clientnum,))

        output = self.run_tahoe('create-client', '--basedir', base)
        self.check_tahoe_output(output, ExpectedCreationOutput, base)

        webportpath = os.path.join(base, 'webport')
        if clientnum == 0:
            # The first client is special:
            self.clientbase = base
            self.port = random.randrange(1024, 2**15)

            f = open(webportpath, 'w')
            f.write('tcp:%d:interface=127.0.0.1\n' % self.port)
            f.close()
            self.weburl = "http://127.0.0.1:%d/" % (self.port,)
            print self.weburl
        else:
            if os.path.exists(webportpath):
                os.remove(webportpath)

        introfurl = os.path.join(introbase, 'introducer.furl')

        self.polling_operation(lambda : os.path.isfile(introfurl),
                               'introducer.furl creation')
        shutil.copy(introfurl, base)

        # NOTE: We assume if tahoe exist with non-zero status, no separate
        # tahoe child process is still running.
        startoutput = self.run_tahoe('start', '--basedir', base)
        try:
            self.check_tahoe_output(startoutput, ExpectedStartOutput, base)

            return self.launch_clients_layer(introbase, clientnum+1)

        finally:
            print 'Stopping client node %d.' % (clientnum,)
            self.stop_node(base)

    def create_test_dirnode_layer(self):
        print 'Creating test dirnode.'

        cap = self.create_dirnode()

        f = open(os.path.join(self.clientbase, 'private', 'root_dir.cap'), 'w')
        f.write(cap)
        f.close()

        return self.mount_fuse_layer(cap)

    def mount_fuse_layer(self, root_uri):
        mpbase = os.path.join(self.testroot, 'mountpoint')
        os.mkdir(mpbase)
        results = []

        if self.config['debug-wait']:
            ImplProcessManager.debug_wait = True

        #for name, kwargs in implementations.items():
        for name in self.config.implementations:
            kwargs = implementations[name]
            #print 'instantiating %s: %r' % (name, kwargs)
            implprocmgr = ImplProcessManager(name, **kwargs)
            print '\n*** Testing impl: %r' % (implprocmgr.name)
            implprocmgr.configure(self.clientbase, mpbase)
            implprocmgr.mount()
            try:
                failures, total = self.run_test_layer(root_uri, implprocmgr)
                result = (implprocmgr.name, failures, total)
                tmpl = '\n*** Test Results implementation %s: %d failed out of %d.'
                print tmpl % result
                results.append(result)
            finally:
                implprocmgr.umount()
        return results

    def run_test_layer(self, root_uri, iman):
        self.maybe_webopen('uri/'+root_uri)
        failures = 0
        testnum = 0
        numtests = 0
        if self.config.tests:
            tests = self.config.tests
        else:
            tests = list(set(self.config.suites).intersection(set(iman.suites)))
        self.maybe_wait('waiting (about to run tests)')
        for test in tests:
            testnames = [n for n in sorted(dir(self)) if n.startswith('test_'+test)]
            numtests += len(testnames)
            print 'running %s %r tests' % (len(testnames), test,)
            for testname in testnames:
                testnum += 1
                print '\n*** Running test #%d: %s' % (testnum, testname)
                try:
                    testcap = self.create_dirnode()
                    dirname = '%s_%s' % (iman.name, testname)
                    self.attach_node(root_uri, testcap, dirname)
                    method = getattr(self, testname)
                    method(testcap, testdir = os.path.join(iman.mountpath, dirname))
                    print 'Test succeeded.'
                except TestFailure, f:
                    print f
                    #print traceback.format_exc()
                    failures += 1
                except:
                    print 'Error in test code...  Cleaning up.'
                    raise
        return (failures, numtests)

    # Tests:
    def test_read_directory_existence(self, testcap, testdir):
        if not wrap_os_error(os.path.isdir, testdir):
            raise TestFailure('Attached test directory not found: %r', testdir)

    def test_read_empty_directory_listing(self, testcap, testdir):
        listing = wrap_os_error(os.listdir, testdir)
        if listing:
            raise TestFailure('Expected empty directory, found: %r', listing)

    def test_read_directory_listing(self, testcap, testdir):
        names = []
        filesizes = {}

        for i in range(3):
            fname = 'file_%d' % (i,)
            names.append(fname)
            body = 'Hello World #%d!' % (i,)
            filesizes[fname] = len(body)

            cap = self.webapi_call('PUT', '/uri', body)
            self.attach_node(testcap, cap, fname)

            dname = 'dir_%d' % (i,)
            names.append(dname)

            cap = self.create_dirnode()
            self.attach_node(testcap, cap, dname)

        names.sort()

        listing = wrap_os_error(os.listdir, testdir)
        listing.sort()

        if listing != names:
            tmpl = 'Expected directory list containing %r but fuse gave %r'
            raise TestFailure(tmpl, names, listing)

        for file, size in filesizes.items():
            st = wrap_os_error(os.stat, os.path.join(testdir, file))
            if st.st_size != size:
                tmpl = 'Expected %r size of %r but fuse returned %r'
                raise TestFailure(tmpl, file, size, st.st_size)

    def test_read_file_contents(self, testcap, testdir):
        name = 'hw.txt'
        body = 'Hello World!'

        cap = self.webapi_call('PUT', '/uri', body)
        self.attach_node(testcap, cap, name)

        path = os.path.join(testdir, name)
        try:
            found = open(path, 'r').read()
        except Exception, err:
            tmpl = 'Could not read file contents of %r: %r'
            raise TestFailure(tmpl, path, err)

        if found != body:
            tmpl = 'Expected file contents %r but found %r'
            raise TestFailure(tmpl, body, found)

    def test_read_in_random_order(self, testcap, testdir):
        sz = 2**20
        bs = 2**10
        assert(sz % bs == 0)
        name = 'random_read_order'
        body = os.urandom(sz)

        cap = self.webapi_call('PUT', '/uri', body)
        self.attach_node(testcap, cap, name)

        # XXX this should also do a test where sz%bs != 0, so that it correctly tests
        # the edge case where the last read is a 'short' block
        path = os.path.join(testdir, name)
        try:
            fsize = os.path.getsize(path)
            if fsize != len(body):
                tmpl = 'Expected file size %s but found %s'
                raise TestFailure(tmpl, len(body), fsize)
        except Exception, err:
            tmpl = 'Could not read file size for %r: %r'
            raise TestFailure(tmpl, path, err)

        try:
            f = open(path, 'r')
            posns = range(0,sz,bs)
            random.shuffle(posns)
            data = [None] * (sz/bs)
            for p in posns:
                f.seek(p)
                data[p/bs] = f.read(bs)
            found = ''.join(data)
        except Exception, err:
            tmpl = 'Could not read file %r: %r'
            raise TestFailure(tmpl, path, err)

        if found != body:
            tmpl = 'Expected file contents %s but found %s'
            raise TestFailure(tmpl, drepr(body), drepr(found))

    def get_file(self, dircap, path):
        body = self.webapi_call('GET', '/uri/%s/%s' % (dircap, path))
        return body

    def test_write_tiny_file(self, testcap, testdir):
        self._write_test_linear(testcap, testdir, name='tiny.junk', bs=2**9, sz=2**9)

    def test_write_linear_small_writes(self, testcap, testdir):
        self._write_test_linear(testcap, testdir, name='large_linear.junk', bs=2**9, sz=2**20)

    def test_write_linear_large_writes(self, testcap, testdir):
        # at least on the mac, large io block sizes are reduced to 64k writes through fuse
        self._write_test_linear(testcap, testdir, name='small_linear.junk', bs=2**18, sz=2**20)

    def _write_test_linear(self, testcap, testdir, name, bs, sz):
        body = os.urandom(sz)
        try:
            path = os.path.join(testdir, name)
            f = file(path, 'w')
        except Exception, err:
            tmpl = 'Could not open file for write at %r: %r'
            raise TestFailure(tmpl, path, err)
        try:
            for posn in range(0,sz,bs):
                f.write(body[posn:posn+bs])
            f.close()
        except Exception, err:
            tmpl = 'Could not write to file %r: %r'
            raise TestFailure(tmpl, path, err)

        self._check_write(testcap, name, body)

    def _check_write(self, testcap, name, expected_body):
        uploaded_body = self.get_file(testcap, name)
        if uploaded_body != expected_body:
            tmpl = 'Expected file contents %s but found %s'
            raise TestFailure(tmpl, drepr(expected_body), drepr(uploaded_body))

    def test_write_overlapping_small_writes(self, testcap, testdir):
        self._write_test_overlap(testcap, testdir, name='large_overlap', bs=2**9, sz=2**20)

    def test_write_overlapping_large_writes(self, testcap, testdir):
        self._write_test_overlap(testcap, testdir, name='small_overlap', bs=2**18, sz=2**20)

    def _write_test_overlap(self, testcap, testdir, name, bs, sz):
        body = os.urandom(sz)
        try:
            path = os.path.join(testdir, name)
            f = file(path, 'w')
        except Exception, err:
            tmpl = 'Could not open file for write at %r: %r'
            raise TestFailure(tmpl, path, err)
        try:
            for posn in range(0,sz,bs):
                start = max(0, posn-bs)
                end = min(sz, posn+bs)
                f.seek(start)
                f.write(body[start:end])
            f.close()
        except Exception, err:
            tmpl = 'Could not write to file %r: %r'
            raise TestFailure(tmpl, path, err)

        self._check_write(testcap, name, body)


    def test_write_random_scatter(self, testcap, testdir):
        sz = 2**20
        name = 'random_scatter'
        body = os.urandom(sz)

        def rsize(sz=sz):
            return min(int(random.paretovariate(.25)), sz/12)

        # first chop up whole file into random sized chunks
        slices = []
        posn = 0
        while posn < sz:
            size = rsize()
            slices.append( (posn, body[posn:posn+size]) )
            posn += size
        random.shuffle(slices) # and randomise their order

        try:
            path = os.path.join(testdir, name)
            f = file(path, 'w')
        except Exception, err:
            tmpl = 'Could not open file for write at %r: %r'
            raise TestFailure(tmpl, path, err)
        try:
            # write all slices: we hence know entire file is ultimately written
            # write random excerpts: this provides for mixed and varied overlaps
            for posn,slice in slices:
                f.seek(posn)
                f.write(slice)
                rposn = random.randint(0,sz)
                f.seek(rposn)
                f.write(body[rposn:rposn+rsize()])
            f.close()
        except Exception, err:
            tmpl = 'Could not write to file %r: %r'
            raise TestFailure(tmpl, path, err)

        self._check_write(testcap, name, body)

    def test_write_partial_overwrite(self, testcap, testdir):
        name = 'partial_overwrite'
        body = '_'*132
        overwrite = '^'*8
        position = 26

        def write_file(path, mode, contents, position=None):
            try:
                f = file(path, mode)
                if position is not None:
                    f.seek(position)
                f.write(contents)
                f.close()
            except Exception, err:
                tmpl = 'Could not write to file %r: %r'
                raise TestFailure(tmpl, path, err)

        def read_file(path):
            try:
                f = file(path, 'rb')
                contents = f.read()
                f.close()
            except Exception, err:
                tmpl = 'Could not read file %r: %r'
                raise TestFailure(tmpl, path, err)
            return contents

        path = os.path.join(testdir, name)
        #write_file(path, 'w', body)

        cap = self.webapi_call('PUT', '/uri', body)
        self.attach_node(testcap, cap, name)

        contents = read_file(path)
        if contents != body:
            raise TestFailure('File contents mismatch (%r) %r v.s. %r', path, contents, body)

        write_file(path, 'r+', overwrite, position)
        contents = read_file(path)
        expected = body[:position] + overwrite + body[position+len(overwrite):]
        if contents != expected:
            raise TestFailure('File contents mismatch (%r) %r v.s. %r', path, contents, expected)


    # Utilities:
    def run_tahoe(self, *args):
        realargs = ('tahoe',) + args
        status, output = gather_output(realargs, executable=self.cliexec)
        if status != 0:
            tmpl = 'The tahoe cli exited with nonzero status.\n'
            tmpl += 'Executable: %r\n'
            tmpl += 'Command arguments: %r\n'
            tmpl += 'Exit status: %r\n'
            tmpl += 'Output:\n%s\n[End of tahoe output.]\n'
            raise SetupFailure(tmpl,
                                    self.cliexec,
                                    realargs,
                                    status,
                                    output)
        return output

    def check_tahoe_output(self, output, expected, expdir):
        ignorable_lines = map(re.compile, [
            '.*site-packages/zope\.interface.*\.egg/zope/__init__.py:3: UserWarning: Module twisted was already imported from .*egg is being added to sys.path',
            '  import pkg_resources',
            ])
        def ignore_line(line):
            for ignorable_line in ignorable_lines:
                if ignorable_line.match(line):
                    return True
            else:
                return False
        output = '\n'.join( [ line 
                              for line in output.split('\n')+['']
                              #if line not in ignorable_lines ] )
                              if not ignore_line(line) ] )
        m = re.match(expected, output, re.M)
        if m is None:
            tmpl = 'The output of tahoe did not match the expectation:\n'
            tmpl += 'Expected regex: %s\n'
            tmpl += 'Actual output: %r\n'
            self.warn(tmpl, expected, output)

        elif expdir != m.group('path'):
            tmpl = 'The output of tahoe refers to an unexpected directory:\n'
            tmpl += 'Expected directory: %r\n'
            tmpl += 'Actual directory: %r\n'
            self.warn(tmpl, expdir, m.group(1))

    def stop_node(self, basedir):
        try:
            self.run_tahoe('stop', '--basedir', basedir)
        except Exception, e:
            print 'Failed to stop tahoe node.'
            print 'Ignoring cleanup exception:'
            # Indent the exception description:
            desc = str(e).rstrip()
            print '  ' + desc.replace('\n', '\n  ')

    def webapi_call(self, method, path, body=None, **options):
        if options:
            path = path + '?' + ('&'.join(['%s=%s' % kv for kv in options.items()]))

        conn = httplib.HTTPConnection('127.0.0.1', self.port)
        conn.request(method, path, body = body)
        resp = conn.getresponse()

        if resp.status != 200:
            tmpl = 'A webapi operation failed.\n'
            tmpl += 'Request: %r %r\n'
            tmpl += 'Body:\n%s\n'
            tmpl += 'Response:\nStatus %r\nBody:\n%s'
            raise SetupFailure(tmpl,
                                    method, path,
                                    body or '',
                                    resp.status, body)

        return resp.read()

    def create_dirnode(self):
        return self.webapi_call('PUT', '/uri', t='mkdir').strip()

    def attach_node(self, dircap, childcap, childname):
        body = self.webapi_call('PUT',
                                '/uri/%s/%s' % (dircap, childname),
                                body = childcap,
                                t = 'uri',
                                replace = 'false')
        assert body.strip() == childcap, `body, dircap, childcap, childname`

    def polling_operation(self, operation, polldesc, timeout = 10.0, pollinterval = 0.2):
        totaltime = timeout # Fudging for edge-case SetupFailure description...

        totalattempts = int(timeout / pollinterval)

        starttime = time.time()
        for attempt in range(totalattempts):
            opstart = time.time()

            try:
                result = operation()
            except KeyboardInterrupt, e:
                raise
            except Exception, e:
                result = False

            totaltime = time.time() - starttime

            if result is not False:
                #tmpl = '(Polling took over %.2f seconds.)'
                #print tmpl % (totaltime,)
                return result

            elif totaltime > timeout:
                break

            else:
                opdelay = time.time() - opstart
                realinterval = max(0., pollinterval - opdelay)

                #tmpl = '(Poll attempt %d failed after %.2f seconds, sleeping %.2f seconds.)'
                #print tmpl % (attempt+1, opdelay, realinterval)
                time.sleep(realinterval)


        tmpl = 'Timeout while polling for: %s\n'
        tmpl += 'Waited %.2f seconds (%d polls).'
        raise SetupFailure(tmpl, polldesc, totaltime, attempt+1)

    def warn(self, tmpl, *args):
        print ('Test Warning: ' + tmpl) % args


# SystemTest Exceptions:
class Failure (Exception):
    def __init__(self, tmpl, *args):
        msg = self.Prefix + (tmpl % args)
        Exception.__init__(self, msg)

class SetupFailure (Failure):
    Prefix = 'Setup Failure - The test framework encountered an error:\n'

class TestFailure (Failure):
    Prefix = 'TestFailure: '


### Unit Tests:
class Impl_A_UnitTests (unittest.TestCase):
    '''Tests small stand-alone functions.'''
    def test_canonicalize_cap(self):
        iopairs = [('http://127.0.0.1:8123/uri/URI:DIR2:yar9nnzsho6czczieeesc65sry:upp1pmypwxits3w9izkszgo1zbdnsyk3nm6h7e19s7os7s6yhh9y',
                    'URI:DIR2:yar9nnzsho6czczieeesc65sry:upp1pmypwxits3w9izkszgo1zbdnsyk3nm6h7e19s7os7s6yhh9y'),
                   ('http://127.0.0.1:8123/uri/URI%3ACHK%3Ak7ktp1qr7szmt98s1y3ha61d9w%3A8tiy8drttp65u79pjn7hs31po83e514zifdejidyeo1ee8nsqfyy%3A3%3A12%3A242?filename=welcome.html',
                    'URI:CHK:k7ktp1qr7szmt98s1y3ha61d9w:8tiy8drttp65u79pjn7hs31po83e514zifdejidyeo1ee8nsqfyy:3:12:242?filename=welcome.html')]

        for input, output in iopairs:
            result = impl_a.canonicalize_cap(input)
            self.failUnlessEqual(output, result, 'input == %r' % (input,))



### Misc:
class ImplProcessManager(object):
    debug_wait = False

    def __init__(self, name, module, mount_args, mount_wait, suites, todo=False):
        self.name = name
        self.module = module
        self.script = module.__file__
        self.mount_args = mount_args
        self.mount_wait = mount_wait
        self.suites = suites
        self.todo = todo

    def maybe_wait(self, msg='waiting'):
        if self.debug_wait:
            print msg
            raw_input()

    def configure(self, client_nodedir, mountpoint):
        self.client_nodedir = client_nodedir
        self.mountpath = os.path.join(mountpoint, self.name)
        os.mkdir(self.mountpath)

    def mount(self):
        print 'Mounting implementation: %s (%s)' % (self.name, self.script)

        rootdirfile = os.path.join(self.client_nodedir, 'private', 'root_dir.cap')
        root_uri = file(rootdirfile, 'r').read().strip()
        fields = {'mountpath': self.mountpath,
                  'nodedir': self.client_nodedir,
                  'root-uri': root_uri,
                 }
        args = ['python', self.script] + [ arg%fields for arg in self.mount_args ]
        print ' '.join(args)
        self.maybe_wait('waiting (about to launch fuse)')

        if self.mount_wait:
            exitcode, output = gather_output(args)
            if exitcode != 0 or output:
                tmpl = '%r failed to launch:\n'
                tmpl += 'Exit Status: %r\n'
                tmpl += 'Output:\n%s\n'
                raise SetupFailure(tmpl, self.script, exitcode, output)
        else:
            self.proc = subprocess.Popen(args)

    def umount(self):
        print 'Unmounting implementation: %s' % (self.name,)
        args = UNMOUNT_CMD + [self.mountpath]
        print args
        self.maybe_wait('waiting (unmount)')
        #print os.system('ls -l '+self.mountpath)
        ec, out = gather_output(args)
        if ec != 0 or out:
            tmpl = '%r failed to unmount:\n' % (' '.join(UNMOUNT_CMD),)
            tmpl += 'Arguments: %r\n'
            tmpl += 'Exit Status: %r\n'
            tmpl += 'Output:\n%s\n'
            raise SetupFailure(tmpl, args, ec, out)


def gather_output(*args, **kwargs):
    '''
    This expects the child does not require input and that it closes
    stdout/err eventually.
    '''
    p = subprocess.Popen(stdout = subprocess.PIPE,
                         stderr = subprocess.STDOUT,
                         *args,
                         **kwargs)
    output = p.stdout.read()
    exitcode = p.wait()
    return (exitcode, output)


def wrap_os_error(meth, *args):
    try:
        return meth(*args)
    except os.error, e:
        raise TestFailure('%s', e)


ExpectedCreationOutput = r'(introducer|client) created in (?P<path>.*?)\n'
ExpectedStartOutput = r'STARTING (?P<path>.*?)\n(introducer|client) node probably started'


if __name__ == '__main__':
    sys.exit(main(sys.argv))
