#! /usr/bin/env python
'''
Unit and system tests for tahoe-fuse.
'''

# Note: It's always a SetupFailure, not a TestFailure if a webapi
# operation fails, because this does not indicate a fuse interface
# failure.

# TODO: Test mismatches between tahoe and fuse/posix.  What about nodes
# with crazy names ('\0', unicode, '/', '..')?  Huuuuge files?
# Huuuuge directories...  As tahoe approaches production quality, it'd
# be nice if the fuse interface did so also by hardening against such cases.

# FIXME: This framework might be replaceable with twisted.trial,
# especially the "layer" design, which is a bit cumbersome when
# using recursion to manage multiple clients.

# FIXME: Identify all race conditions (hint: starting clients, versus
# using the grid fs).

import sys, os, shutil, unittest, subprocess
import tempfile, re, time, signal, random, httplib
import traceback

import tahoe_fuse


### Main flow control:
def main(args = sys.argv[1:]):
    target = 'all'
    if args:
        if len(args) != 1:
            raise SystemExit(Usage)
        target = args[0]

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
            failures, total = self.init_cli_layer()
            print '\n*** System Tests complete: %d failed out of %d.' % (failures, total)           
        except self.SetupFailure, sfail:
            print
            print sfail
            print '\n*** System Tests were not successfully completed.' 

    def init_cli_layer(self):
        '''This layer finds the appropriate tahoe executable.'''
        runtestpath = os.path.abspath(sys.argv[0])
        path = runtestpath
        for expectedname in ('runtests.py', 'fuse', 'contrib'):
            path, name = os.path.split(path)

            if name != expectedname:
                reason = 'Unexpected test script path: %r\n'
                reason += 'The system test script must be run from the source directory.'
                raise self.SetupFailure(reason, runtestpath)

        self.cliexec = os.path.join(path, 'bin', 'tahoe')
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
    def launch_clients_layer(self, introbase, clientnum = 1):
        if clientnum > self.TotalClientsNeeded:
            return self.create_test_dirnode_layer()

        tmpl = 'Launching client %d of %d.'
        print tmpl % (clientnum,
                      self.TotalClientsNeeded)

        base = os.path.join(self.testroot, 'client_%d' % (clientnum,))

        output = self.run_tahoe('create-client', '--basedir', base)
        self.check_tahoe_output(output, ExpectedCreationOutput, base)

        if clientnum == 1:
            # The first client is special:
            self.clientbase = base
            self.port = random.randrange(1024, 2**15)

            f = open(os.path.join(base, 'webport'), 'w')
            f.write('tcp:%d:interface=127.0.0.1\n' % self.port)
            f.close()

        introfurl = os.path.join(introbase, 'introducer.furl')

        self.polling_operation(lambda : os.path.isfile(introfurl))
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

        targeturl = 'http://127.0.0.1:%d/uri?t=mkdir' % (self.port,)

        def make_dirnode():
            conn = httplib.HTTPConnection('127.0.0.1', self.port)
            conn.request('PUT', '/uri?t=mkdir')
            resp = conn.getresponse()
            if resp.status == 200:
                return resp.read().strip()
            else:
                # FIXME: This output can be excessive!
                print 'HTTP %r reponse while attempting to make node.' % (resp.status,)
                print resp.read()
                return False # make another polling attempt...
            
        cap = self.polling_operation(make_dirnode)

        f = open(os.path.join(self.clientbase, 'private', 'root_dir.cap'), 'w')
        f.write(cap)
        f.close()

        return self.mount_fuse_layer(cap)
        
    def mount_fuse_layer(self):
        print 'Mounting fuse interface.'

        mp = os.path.join(self.testroot, 'mountpoint')
        os.mkdir(mp)

        thispath = os.path.abspath(sys.argv[0])
        thisdir = os.path.dirname(thispath)
        fusescript = os.path.join(thisdir, 'tahoe_fuse.py')
        try:
            proc = subprocess.Popen([fusescript,
                                     mp,
                                     '-f',
                                     '--basedir', self.clientbase])

            # The mount is verified by the test_layer, but we sleep to
            # avoid race conditions against the first few tests.
            time.sleep(fusepause)

            return self.run_test_layer(fusebasecap, mp)
                
        finally:
            print '\n*** Cleaning up system test'

            if proc.poll() is None:
                print 'Killing fuse interface.'
                os.kill(proc.pid, signal.SIGTERM)
                print 'Waiting for the fuse interface to exit.'
                proc.wait()
            
    def run_test_layer(self, mountpoint):
        total = failures = 0
        for name in sorted(dir(self)):
            if name.startswith('test_'):
                total += 1
                print '\n*** Running test #%d: %s' % (total, name)
                try:
                    method = getattr(self, name)
                    method(mountpoint)
                    print 'Test succeeded.'
                except self.TestFailure, f:
                    print f
                    failures += 1
                except:
                    print 'Error in test code...  Cleaning up.'
                    raise

        return (failures, total)


    # Tests:
    def test_00_empty_directory_listing(self, mountpoint):
        listing = os.listdir(mountpoint)
        if listing:
            raise self.TestFailure('Expected empty directory, found: %r' % (listing,))
    
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

    def polling_operation(self, operation, timeout = 10.0, pollinterval = 0.2):
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

        tmpl = 'Timeout after waiting for creation of introducer.furl.\n'
        tmpl += 'Waited %.2f seconds (%d polls).'
        raise self.SetupFailure(tmpl, totaltime, attempt+1)

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
class TestUtilFunctions (unittest.TestCase):
    '''Tests small stand-alone functions.'''
    def test_canonicalize_cap(self):
        iopairs = [('http://127.0.0.1:8123/uri/URI:DIR2:yar9nnzsho6czczieeesc65sry:upp1pmypwxits3w9izkszgo1zbdnsyk3nm6h7e19s7os7s6yhh9y',
                    'URI:DIR2:yar9nnzsho6czczieeesc65sry:upp1pmypwxits3w9izkszgo1zbdnsyk3nm6h7e19s7os7s6yhh9y'),
                   ('http://127.0.0.1:8123/uri/URI%3ACHK%3Ak7ktp1qr7szmt98s1y3ha61d9w%3A8tiy8drttp65u79pjn7hs31po83e514zifdejidyeo1ee8nsqfyy%3A3%3A12%3A242?filename=welcome.html',
                    'URI:CHK:k7ktp1qr7szmt98s1y3ha61d9w:8tiy8drttp65u79pjn7hs31po83e514zifdejidyeo1ee8nsqfyy:3:12:242?filename=welcome.html')]

        for input, output in iopairs:
            result = tahoe_fuse.canonicalize_cap(input)
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
