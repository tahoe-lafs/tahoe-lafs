#!/usr/bin/env python

"""
Given a list of nodeids and a 'convergence' file, create a bunch of files
that will (when encoded at k=1,N=1) be uploaded to specific nodeids.

Run this as follows:

 make-canary-files.py -c PATH/TO/convergence -n PATH/TO/nodeids -k 1 -N 1

It will create a directory named 'canaries', with one file per nodeid named
'$NODEID-$NICKNAME.txt', that contains some random text.

The 'nodeids' file should contain one base32 nodeid per line, followed by the
optional nickname, like:

---
5yyqu2hbvbh3rgtsgxrmmg4g77b6p3yo  server12
vb7vm2mneyid5jbyvcbk2wb5icdhwtun  server13
...
---

The resulting 'canaries/5yyqu2hbvbh3rgtsgxrmmg4g77b6p3yo-server12.txt' file
will, when uploaded with the given (convergence,k,N) pair, have its first
share placed on the 5yyq/server12 storage server. If N>1, the other shares
will be placed elsewhere, of course.

This tool can be useful to construct a set of 'canary' files, which can then
be uploaded to storage servers, and later downloaded to test a grid's health.
If you are able to download the canary for server12 via some tahoe node X,
then the following properties are known to be true:

 node X is running, and has established a connection to server12
 server12 is running, and returning data for at least the given file

Using k=1/N=1 creates a separate test for each server. The test process is
then to download the whole directory of files (perhaps with a t=deep-check
operation).

Alternatively, you could upload with the usual k=3/N=10 and then move/delete
shares to put all N shares on a single server.

Note that any changes to the nodeid list will affect the placement of shares.
Shares should be uploaded with the same nodeid list as this tool used when
constructing the files.

Also note that this tool uses the Tahoe codebase, so it should be run on a
system where Tahoe is installed, or in a source tree with setup.py like this:

 setup.py run_with_pythonpath -p -c 'misc/make-canary-files.py ARGS..'
"""

import os, sha
from twisted.python import usage
from allmydata.immutable import upload
from allmydata.util import base32

class Options(usage.Options):
    optParameters = [
        ("convergence", "c", None, "path to NODEDIR/private/convergence"),
        ("nodeids", "n", None, "path to file with one base32 nodeid per line"),
        ("k", "k", 1, "number of necessary shares, defaults to 1", int),
        ("N", "N", 1, "number of total shares, defaults to 1", int),
        ]
    optFlags = [
        ("verbose", "v", "Be noisy"),
        ]

opts = Options()
opts.parseOptions()

verbose = bool(opts["verbose"])

nodes = {}
for line in open(opts["nodeids"], "r").readlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    pieces = line.split(None, 1)
    if len(pieces) == 2:
        nodeid_s, nickname = pieces
    else:
        nodeid_s = pieces[0]
        nickname = None
    nodeid = base32.a2b(nodeid_s)
    nodes[nodeid] = nickname

if opts["k"] != 3 or opts["N"] != 10:
    print "note: using non-default k/N requires patching the Tahoe code"
    print "src/allmydata/client.py line 55, DEFAULT_ENCODING_PARAMETERS"

convergence_file = os.path.expanduser(opts["convergence"])
convergence_s = open(convergence_file, "rb").read().strip()
convergence = base32.a2b(convergence_s)

def get_permuted_peers(key):
    results = []
    for nodeid in nodes:
        permuted = sha.new(key + nodeid).digest()
        results.append((permuted, nodeid))
    results.sort(lambda a,b: cmp(a[0], b[0]))
    return [ r[1] for r in results ]

def find_share_for_target(target):
    target_s = base32.b2a(target)
    prefix = "The first share of this file will be placed on " + target_s + "\n"
    prefix += "This data is random: "
    attempts = 0
    while True:
        attempts += 1
        suffix = base32.b2a(os.urandom(10))
        if verbose: print " trying", suffix,
        data = prefix + suffix + "\n"
        assert len(data) > 55  # no LIT files
        # now, what storage index will this get?
        u = upload.Data(data, convergence)
        eu = upload.EncryptAnUploadable(u)
        d = eu.get_storage_index() # this happens to run synchronously
        def _got_si(si):
            if verbose: print "SI", base32.b2a(si),
            peerlist = get_permuted_peers(si)
            if peerlist[0] == target:
                # great!
                if verbose: print "  yay!"
                fn = base32.b2a(target)
                if nodes[target]:
                    nickname = nodes[target].replace("/", "_")
                    fn += "-" + nickname
                fn += ".txt"
                fn = os.path.join("canaries", fn)
                open(fn, "w").write(data)
                return True
            # nope, must try again
            if verbose: print "  boo"
            return False
        d.addCallback(_got_si)
        # get sneaky and look inside the Deferred for the synchronous result
        if d.result:
            return attempts

os.mkdir("canaries")
attempts = []
for target in nodes:
    target_s = base32.b2a(target)
    print "working on", target_s
    attempts.append(find_share_for_target(target))
print "done"
print "%d attempts total, avg %d per target, max %d" % \
      (sum(attempts), 1.0* sum(attempts) / len(nodes), max(attempts))


