#! /usr/bin/env python
'''
Unit tests for tahoe-fuse.

Note: The API design of the python-fuse library makes unit testing much
of tahoe-fuse.py tricky business.
'''

# FIXME: This framework might be replaceable with twisted.trial,
# especially the "layer" design, which is a bit cumbersome when
# using recursion to manage multiple clients.

# FIXME: Identify all race conditions (hint: starting clients, versus
# using the grid fs).

import sys, os, shutil, unittest, subprocess
import tempfile, re, time, signal, random, httplib

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
        self.cliexec = None
        self.introbase = None
        self.mountpoint = None

        # We keep track of multiple clients for a full-fledged grid in
        # clientsinfo (see SystemTest.ClientInfo).

        # self.clientsinfo[0] is the client to which we attach fuse and
        # make webapi calls (see SystemTest.get_interface_client).
        
        self.clientsinfo = []

    ## Top-level flow control:
    # These "*_layer" methods call eachother in a linear fashion, using
    # exception unwinding to do cleanup properly.  Each "layer" invokes
    # a deeper layer, and each layer does its own cleanup upon exit.
    
    def run(self):
        print 'Running System Test.'
        try:
            self.init_cli_layer()
        except self.SetupFailure, sfail:
            print
            print sfail

        print 'System Test complete.'

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

        self.create_introducer_layer()
        
    def create_introducer_layer(self):
        print 'Creating introducer.'
        self.introbase = tempfile.mkdtemp(prefix='tahoe_fuse_test_',
                                          suffix='_introducer')
        try:
            output = self.run_tahoe('create-introducer', '--basedir', self.introbase)

            pat = r'^introducer created in (.*?)\n\s*$'
            self.check_tahoe_output(output, pat, self.introbase)

            self.launch_introducer_layer()
            
        finally:
            print 'Removing introducer directory.'
            self.cleanup_dir(self.introbase)
    
    def launch_introducer_layer(self):
        print 'Launching introducer.'
        # NOTE: We assume if tahoe exist with non-zero status, no separate
        # tahoe child process is still running.
        output = self.run_tahoe('start', '--basedir', self.introbase)
        try:
            pat = r'^STARTING (.*?)\nintroducer node probably started\s*$'
            self.check_tahoe_output(output, pat, self.introbase)

            self.create_clients_layer()
            
        finally:
            print 'Stopping introducer node.'
            try:
                output = self.run_tahoe('stop', '--basedir', self.introbase)
            except Exception, e:
                print 'Failed to stop introducer node.  Output:'
                print output
                print 'Ignoring cleanup exception: %r' % (e,)
        
    TotalClientsNeeded = 3
    def create_clients_layer(self, clientnum = 0):
        if clientnum == self.TotalClientsNeeded:
            self.launch_clients_layer()
            return

        tmpl = 'Creating client %d of %d.'
        print tmpl % (clientnum + 1,
                      self.TotalClientsNeeded)

        assert len(self.clientsinfo) == clientnum, `clientnum`

        client = self.ClientInfo(clientnum)
        self.clientsinfo.append(client)

        try:
            output = self.run_tahoe('create-client', '--basedir', client.base)
            pat = r'^client created in (.*?)\n'
            pat += r' please copy introducer.furl into the directory\s*$'
            self.check_tahoe_output(output, pat, client.base)

            client.port = random.randrange(1024, 2**15)

            f = open(os.path.join(client.base, 'webport'), 'w')
            f.write('tcp:%d:interface=127.0.0.1\n' % client.port)
            f.close()

            introfurl = os.path.join(self.introbase, 'introducer.furl')

            # FIXME: Is there a better way to handle this race condition?
            self.polling_operation(lambda : os.path.isfile(introfurl))
            shutil.copy(introfurl, client.base)

            self.create_clients_layer(clientnum+1)
            
        finally:
            print 'Removing client %d base directory.' % (clientnum+1,)
            self.cleanup_dir(client.base)
    
    def launch_clients_layer(self, clientnum = 0):
        if clientnum == self.TotalClientsNeeded:
            self.create_test_dirnode_layer()
            return

        tmpl = 'Launching client %d of %d.'
        print tmpl % (clientnum + 1,
                      self.TotalClientsNeeded)

        client = self.clientsinfo[clientnum]

        # NOTE: We assume if tahoe exist with non-zero status, no separate
        # tahoe child process is still running.
        output = self.run_tahoe('start', '--basedir', client.base)
        try:
            pat = r'^STARTING (.*?)\nclient node probably started\s*$'
            self.check_tahoe_output(output, pat, client.base)

            self.launch_clients_layer(clientnum+1)

        finally:
            print 'Stopping client node %d.' % (clientnum+1,)
            try:
                output = self.run_tahoe('stop', '--basedir', client.base)
            except Exception, e:
                print 'Failed to stop client node.  Output:'
                print output
                print 'Ignoring cleanup exception: %r' % (e,)
        
    def create_test_dirnode_layer(self):
        print 'Creating test dirnode.'
        client = self.get_interface_client()

        targeturl = 'http://127.0.0.1:%d/uri?t=mkdir' % (client.port,)

        def make_dirnode():
            conn = httplib.HTTPConnection('127.0.0.1', client.port)
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

        f = open(os.path.join(client.base, 'private', 'root_dir.cap'), 'w')
        f.write(cap)
        f.close()

        self.mount_fuse_layer()
        
    def mount_fuse_layer(self):
        # FIXME - tahoe_fuse.py: This probably currently fails because
        # tahoe_fuse looks in ~/.tahoe.
        
        print 'Mounting fuse interface.'
        self.mountpoint = tempfile.mkdtemp(prefix='tahoe_fuse_mp_')
        try:
            thispath = os.path.abspath(sys.argv[0])
            thisdir = os.path.dirname(thispath)
            fusescript = os.path.join(thisdir, 'tahoe_fuse.py')
            try:
                proc = subprocess.Popen([fusescript, self.mountpoint, '-f'])
                # FIXME: Verify the mount somehow?

                self.run_test_layer()
                
            finally:
                if proc.poll() is None:
                    print 'Killing fuse interface.'
                    os.kill(proc.pid, signal.SIGTERM)
                    print 'Waiting for the fuse interface to exit.'
                    proc.wait()
        finally:
            self.cleanup_dir(self.mountpoint)
            
    def run_test_layer(self):
        raise NotImplementedError()
        

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
            raise self.SetupFailure(tmpl, expected, output)

        if expdir != m.group(1):
            tmpl = 'The output of tahoe refers to an unexpected directory:\n'
            tmpl += 'Expected directory: %r\n'
            tmpl += 'Actual directory: %r\n'
            raise self.SetupFailure(tmpl, expdir, m.group(1))

    def cleanup_dir(self, path):
        try:
            shutil.rmtree(path)
        except Exception, e:
            print 'Exception removing test directory: %r' % (path,)
            print 'Ignoring cleanup exception: %r' % (e,)

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
                tmpl = '(Polling for this condition took over %.2f seconds.)'
                print tmpl % (totaltime,)
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

    def get_interface_client(self):
        return self.clientsinfo[0]

    # ClientInfo:
    class ClientInfo (object):
        def __init__(self, clientnum):
            self.num = clientnum
            self.base = tempfile.mkdtemp(prefix='tahoe_fuse_test_client',
                                         suffix='_%d' % clientnum)
            self.port = None
            
    # SystemTest Exceptions:
    class Failure (Exception):
        pass
    
    class SetupFailure (Failure):
        def __init__(self, tmpl, *args):
            msg = 'SystemTest.SetupFailure - A test environment could not be created:\n'
            msg += tmpl % args
            SystemTest.Failure.__init__(self, msg)


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
    
    
Usage = '''
Usage: %s [target]

Run tests for the given target.

target is one of: unit, system, or all
''' % (sys.argv[0],)



if __name__ == '__main__':
    main()
