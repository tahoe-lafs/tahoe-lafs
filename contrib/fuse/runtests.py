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
import tempfile, re, time, signal, random, httplib
import traceback

# Import fuse implementations:
FuseDir = os.path.join('.', 'contrib', 'fuse')
if not os.path.isdir(FuseDir):
    raise SystemExit('''
Could not find directory "%s".  Please run this script from the tahoe
source base directory.
''' % (FuseDir,))

sys.path.append(os.path.join(FuseDir, 'impl_a'))
import tahoe_fuse as impl_a

sys.path.append(os.path.join(FuseDir, 'impl_b'))
import pyfuse.tahoe as impl_b


### Main flow control:
def main(args = sys.argv):
    target = 'all'
    if len(args) > 1:
        target = args.pop(1)

    if target not in ('all', 'unit', 'system'):
        raise SystemExit(Usage)
        
    if target in ('all', 'unit'):
        run_unit_tests()

    if target in ('all', 'system'):
        run_system_test()


def run_unit_tests():
    print 'Running Unit Tests.'
    try:
        unittest.main()
    except SystemExit, se:
        pass
    print 'Unit Tests complete.\n'
    

def run_system_test():
    SystemTest().run()


### System Testing:
class SystemTest (object):
    def __init__(self):
        # These members represent configuration:
        self.fullcleanup = False # FIXME: Make this a commandline option.
        
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
    
    def run(self, fullcleanup = False):
        '''
        If full_cleanup, delete all temporary state.
        Else:  If there is an error do not delete basedirs.

        Set to False if you wish to analyze a failure.
        '''
        self.fullcleanup = fullcleanup
        print '\n*** Setting up system tests.'
        try:
            results = self.init_cli_layer()
            print '\n*** System Tests complete:'
            for implpath, failures, total in reuslts:
                print 'Implementation %r: %d failed out of %d.' % (implpath, failures, total)           
        except self.SetupFailure, sfail:
            print
            print sfail
            print '\n*** System Tests were not successfully completed.' 

    def init_cli_layer(self):
        '''This layer finds the appropriate tahoe executable.'''
        self.cliexec = os.path.join('.', 'bin', 'tahoe')
        version = self.run_tahoe('--version')
        print 'Using %r with version:\n%s' % (self.cliexec, version.rstrip())

        return self.create_testroot_layer()

    def create_testroot_layer(self):
        print 'Creating test base directory.'
        self.testroot = tempfile.mkdtemp(prefix='tahoe_fuse_test_')
        try:
            return self.launch_introducer_layer()
        finally:
            if self.fullcleanup:
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
            return self.create_test_dirnode_layer()

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
        else:
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
        
    def mount_fuse_layer(self, fusebasecap):
        mpbase = os.path.join(self.testroot, 'mountpoint')
        os.mkdir(mpbase)

        results = []

        # Mount and test each implementation:
        for implnum, implmod in enumerate([impl_a, impl_b]):
            implpath = implmod.__file__
            print '\n*** Mounting and Testing implementation #%d: %r' % (implnum, implpath)

            print 'Mounting implementation #%d: %r' % (implnum, implpath)
            mountpath = os.path.join(mpbase, 'impl_%d' % (implnum,))
            os.mkdir(mountpath)

            exitcode, output = gather_output(['python',
                                              implpath,
                                              mountpath,
                                              '--basedir', self.clientbase])

            if exitcode != 0 or output:
                tmpl = '%r failed to launch:\n'
                tmpl += 'Exit Status: %r\n'
                tmpl += 'Output:\n%s\n'
                raise self.SetupFailure(tmpl, implpath, exitcode, output)

            try:
                failures, total = self.run_test_layer(fusebasecap, mountpath)
                print '\n*** Test results for implementation %r: %d failed out of %d.' % (implpath, failures, total)           
                results.append((implpath, failures, total))

            finally:
                print 'Unmounting implementation #%d' % (implnum,)
                args = ['fusermount', '-u', mountpath]
                ec, out = gather_output(args)
                if ec != 0 or out:
                    tmpl = 'fusermount failed to unmount:\n'
                    tmpl += 'Arguments: %r\n'
                    tmpl += 'Exit Status: %r\n'
                    tmpl += 'Output:\n%s\n'
                    raise self.SetupFailure(tmpl, args, ec, out)

        return results

    def run_test_layer(self, fbcap, mp):
        total = failures = 0
        testnames = [n for n in sorted(dir(self)) if n.startswith('test_')]
        for name in testnames:
            total += 1
            print '\n*** Running test #%d: %s' % (total, name)
            try:
                testcap = self.create_dirnode()
                self.attach_node(fbcap, testcap, name)
                    
                method = getattr(self, name)
                method(testcap, testdir = os.path.join(mp, name))
                print 'Test succeeded.'
            except self.TestFailure, f:
                print f
                failures += 1
            except:
                print 'Error in test code...  Cleaning up.'
                raise

        return (failures, total)


    # Tests:
    def test_directory_existence(self, testcap, testdir):
        if not os.path.isdir(testdir):
            raise self.TestFailure('Attached test directory not found: %r', testdir)
            
    def test_empty_directory_listing(self, testcap, testdir):
        listing = os.listdir(testdir)
        if listing:
            raise self.TestFailure('Expected empty directory, found: %r', listing)
    
    def test_directory_listing(self, testcap, testdir):
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
            
        listing = os.listdir(testdir)
        listing.sort()
        if listing != names:
            tmpl = 'Expected directory list containing %r but fuse gave %r'
            raise self.TestFailure(tmpl, names, listing)

        for file, size in filesizes.items():
            st = os.stat(os.path.join(testdir, file))
            if st.st_size != size:
                tmpl = 'Expected %r size of %r but fuse returned %r'
                raise self.TestFailure(tmpl, file, size, st.st_size)
    
    def test_file_contents(self, testcap, testdir):
        name = 'hw.txt'
        body = 'Hello World!'
            
        cap = self.webapi_call('PUT', '/uri', body)
        self.attach_node(testcap, cap, name)

        path = os.path.join(testdir, name)
        try:
            found = open(path, 'r').read()
        except Exception, err:
            tmpl = 'Could not read file contents of %r: %r'
            raise self.TestFailure(tmpl, path, err)

        if found != body:
            tmpl = 'Expected file contents %r but found %r'
            raise self.TestFailure(tmpl, body, found)
        
            
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
            raise self.SetupFailure(tmpl,
                                    self.cliexec,
                                    realargs,
                                    status,
                                    output)
        return output
    
    def check_tahoe_output(self, output, expected, expdir):
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
            raise self.SetupFailure(tmpl,
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
        assert body.strip() == childcap, `status, dircap, childcap, childname`

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
        raise self.SetupFailure(tmpl, polldesc, totaltime, attempt+1)

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
    

ExpectedCreationOutput = r'(introducer|client) created in (?P<path>.*?)\n'
ExpectedStartOutput = r'STARTING (?P<path>.*?)\n(introducer|client) node probably started'


Usage = '''
Usage: %s [target]

Run tests for the given target.

target is one of: unit, system, or all
''' % (sys.argv[0],)



if __name__ == '__main__':
    main()
