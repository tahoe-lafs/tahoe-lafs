#! /usr/bin/python

"""
Test an existing Tahoe grid, both to see if the grid is still running and to
see if the client is still compatible with it. This script is suitable for
running from a periodic monitoring script, perhaps by an hourly cronjob.

This script uses a pre-established client node (configured to connect to the
grid being tested) and a pre-established directory (stored as the 'testgrid:'
alias in that client node's aliases file). It then performs a number of
uploads and downloads to exercise compatibility in various directions (new
client vs old data). All operations are performed by invoking various CLI
commands through bin/tahoe . The script must be given two arguments: the
client node directory, and the location of the bin/tahoe executable. Note
that this script does not import anything from tahoe directly, so it doesn't
matter what its PYTHONPATH is, as long as the bin/tahoe that it uses is
functional.

This script expects that the client node will be not running when the script
starts, but it will forcibly shut down the node just to be sure. It will shut
down the node after the test finishes.

To set up the client node, do the following:

  tahoe create-client DIR
  touch DIR/no_storage
  populate DIR/introducer.furl
  tahoe start DIR
  tahoe add-alias -d DIR testgrid `tahoe mkdir -d DIR`
  pick a 10kB-ish test file, compute its md5sum
  tahoe put -d DIR FILE testgrid:old.MD5SUM
  tahoe put -d DIR FILE testgrid:recent.MD5SUM
  tahoe put -d DIR FILE testgrid:recentdir/recent.MD5SUM
  echo "" | tahoe put -d DIR --mutable testgrid:log
  echo "" | tahoe put -d DIR --mutable testgrid:recentlog

This script will perform the following steps (the kind of compatibility that
is being tested is in [brackets]):

 read old.* and check the md5sums [confirm that new code can read old files]
 read all recent.* files and check md5sums [read recent files]
 delete all recent.* files and verify they're gone [modify an old directory]
 read recentdir/recent.* files and check [read recent directory]
 delete recentdir/recent.* and verify [modify recent directory]
 delete recentdir and verify (keep the directory from growing unboundedly)
 mkdir recentdir
 upload random 10kB file to recentdir/recent.MD5SUM (prepare for next time)
 upload random 10kB file to recent.MD5SUM [new code can upload to old servers]
 append one-line timestamp to log [read/write old mutable files]
 append one-line timestamp to recentlog [read/write recent mutable files]
 delete recentlog
 upload small header to new mutable recentlog [create mutable files]

This script will also keep track of speeds and latencies and will write them
in a machine-readable logfile.

"""

import time, subprocess, md5, os.path, random
from twisted.python import usage

class GridTesterOptions(usage.Options):

    optFlags = [
        ("no", "n", "Dry run: do not run any commands, just print them."),
        ]

    def parseArgs(self, nodedir, tahoe):
        self.nodedir = nodedir
        self.tahoe = os.path.abspath(tahoe)

class CommandFailed(Exception):
    pass

class GridTester:
    def __init__(self, config):
        self.config = config
        self.tahoe = config.tahoe
        self.nodedir = config.nodedir

    def command(self, *cmd, **kwargs):
        expected_rc = kwargs.get("expected_rc", None)
        stdin = kwargs.get("stdin", None)
        if self.config["no"]:
            return
        if stdin is not None:
            p = subprocess.Popen(cmd,
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            (stdout,stderr) = p.communicate(stdin)
        else:
            p = subprocess.Popen(cmd,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            (stdout,stderr) = p.communicate()
        rc = p.returncode
        if expected_rc != None and rc != expected_rc:
            raise CommandFailed("command '%s' failed: rc=%d" % (cmd, rc))
        return stdout, stderr

    def cli(self, cmd, *args, **kwargs):
        print "tahoe", cmd, " ".join(args)
        stdout, stderr = self.command(self.tahoe, cmd, "-d", self.nodedir,
                                      *args, **kwargs)
        if not kwargs.get("ignore_stderr", False) and stderr != "":
            raise CommandFailed("command '%s' had stderr: %s" % (" ".join(args),
                                                                 stderr))
        return stdout

    def stop_old_node(self):
        print "tahoe stop", self.nodedir, "(force)"
        self.command(self.tahoe, "stop", self.nodedir, expected_rc=None)

    def start_node(self):
        print "tahoe start", self.nodedir
        self.command(self.tahoe, "start", self.nodedir)
        time.sleep(5)

    def stop_node(self):
        print "tahoe stop", self.nodedir
        self.command(self.tahoe, "stop", self.nodedir)

    def read_and_check(self, f):
        expected_md5_s = f[f.find(".")+1:]
        out = self.cli("get", "testgrid:" + f)
        got_md5_s = md5.new(out).hexdigest()
        if got_md5_s != expected_md5_s:
            raise CommandFailed("%s had md5sum of %s" % (f, got_md5_s))

    def delete_and_check(self, dirname, f):
        oldfiles = self.listdir(dirname)
        if dirname:
            absfilename = "testgrid:" + dirname + "/" + f
        else:
            absfilename = "testgrid:" + f
        if f not in oldfiles:
            raise CommandFailed("um, '%s' was supposed to already be in %s"
                                % (f, dirname))
        self.cli("rm", absfilename)
        newfiles = self.listdir(dirname)
        if f in newfiles:
            raise CommandFailed("failed to remove '%s' from %s" % (f, dirname))

    def listdir(self, dirname):
        out = self.cli("ls", "testgrid:"+dirname).strip().split("\n")
        files = [f.strip() for f in out]
        print " ", files
        return files

    def do_test(self):
        files = self.listdir("")
        for f in files:
            if f.startswith("old.") or f.startswith("recent."):
                self.read_and_check("" + f)
        for f in files:
            if f.startswith("recent."):
                self.delete_and_check("", f)
        files = self.listdir("recentdir")
        for f in files:
            if f.startswith("old.") or f.startswith("recent."):
                self.read_and_check("recentdir/" + f)
        for f in files:
            if f.startswith("recent."):
                self.delete_and_check("recentdir", f)
        self.delete_and_check("", "recentdir")

        self.cli("mkdir", "testgrid:recentdir")
        fn, data = self.makefile("recent")
        self.put("recentdir/"+fn, data)
        files = self.listdir("recentdir")
        if fn not in files:
            raise CommandFailed("failed to put %s in recentdir/" % fn)
        fn, data = self.makefile("recent")
        self.put(fn, data)
        files = self.listdir("")
        if fn not in files:
            raise CommandFailed("failed to put %s in testgrid:" % fn)

        self.update("log")
        self.update("recentlog")
        self.delete_and_check("", "recentlog")
        self.put_mutable("recentlog", "Recent Mutable Log Header\n\n")

    def put(self, fn, data):
        self.cli("put", "-", "testgrid:"+fn, stdin=data, ignore_stderr=True)

    def put_mutable(self, fn, data):
        self.cli("put", "--mutable", "-", "testgrid:"+fn,
                 stdin=data, ignore_stderr=True)

    def update(self, fn):
        old = self.cli("get", "testgrid:"+fn)
        new = old + time.ctime() + "\n"
        self.put(fn, new)

    def makefile(self, prefix):
        size = random.randint(10001, 10100)
        data = os.urandom(size)
        md5sum = md5.new(data).hexdigest()
        fn = prefix + "." + md5sum
        return fn, data

    def run(self):
        self.stop_old_node()
        self.start_node()
        try:
            self.do_test()
        finally:
            self.stop_node()

def main():
    config = GridTesterOptions()
    config.parseOptions()
    gt = GridTester(config)
    gt.run()

if __name__ == "__main__":
    main()
