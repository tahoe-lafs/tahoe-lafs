#! /usr/bin/python

# used to discuss ticket #302: "stop permuting peerlist?"

import time
import math
from hashlib import sha1, md5, sha256
myhash = md5
# md5: 1520 "uploads" per second
# sha1: 1350 ups
# sha256: 930 ups
from itertools import count
from twisted.python import usage

def abbreviate_space(s, SI=True):
    if s is None:
        return "unknown"
    if SI:
        U = 1000.0
        isuffix = "B"
    else:
        U = 1024.0
        isuffix = "iB"
    def r(count, suffix):
        return "%.2f %s%s" % (count, suffix, isuffix)

    if s < 1024: # 1000-1023 get emitted as bytes, even in SI mode
        return "%d B" % s
    if s < U*U:
        return r(s/U, "k")
    if s < U*U*U:
        return r(s/(U*U), "M")
    if s < U*U*U*U:
        return r(s/(U*U*U), "G")
    if s < U*U*U*U*U:
        return r(s/(U*U*U*U), "T")
    return r(s/(U*U*U*U*U), "P")

def make_up_a_file_size(seed):
    h = int(myhash(seed).hexdigest(),16)
    max=2**31
    if 1: # exponential distribution
        e = 8 + (h % (31-8))
        return 2 ** e
    # uniform distribution
    return h % max # avg 1GB

sizes = [make_up_a_file_size(str(i)) for i in range(10000)]
avg_filesize = sum(sizes)/len(sizes)
print "average file size:", abbreviate_space(avg_filesize)

SERVER_CAPACITY = 10**12

class Server:
    def __init__(self, nodeid, capacity):
        self.nodeid = nodeid
        self.used = 0
        self.capacity = capacity
        self.numshares = 0
        self.full_at_tick = None

    def upload(self, sharesize):
        if self.used + sharesize < self.capacity:
            self.used += sharesize
            self.numshares += 1
            return True
        return False

    def __repr__(self):
        if self.full_at_tick is not None:
            return "<%s %s full at %d>" % (self.__class__.__name__, self.nodeid, self.full_at_tick)
        else:
            return "<%s %s>" % (self.__class__.__name__, self.nodeid)

class Ring:
    SHOW_MINMAX = False
    def __init__(self, numservers, seed, permute):
        self.servers = []
        for i in range(numservers):
            nodeid = myhash(str(seed)+str(i)).hexdigest()
            capacity = SERVER_CAPACITY
            s = Server(nodeid, capacity)
            self.servers.append(s)
        self.servers.sort(key=lambda s: s.nodeid)
        self.permute = permute
        #self.list_servers()

    def list_servers(self):
        for i in range(len(self.servers)):
            s = self.servers[i]
            next_s = self.servers[(i+1)%len(self.servers)]
            diff = "%032x" % (int(next_s.nodeid,16) - int(s.nodeid,16))
            s.next_diff = diff
            prev_s = self.servers[(i-1)%len(self.servers)]
            diff = "%032x" % (int(s.nodeid,16) - int(prev_s.nodeid,16))
            s.prev_diff = diff
            print s, s.prev_diff

        print "sorted by delta"
        for s in sorted(self.servers, key=lambda s:s.prev_diff):
            print s, s.prev_diff

    def servers_for_si(self, si):
        if self.permute:
            def sortkey(s):
                return myhash(s.nodeid+si).digest()
            return sorted(self.servers, key=sortkey)
        for i in range(len(self.servers)):
            if self.servers[i].nodeid >= si:
                return self.servers[i:] + self.servers[:i]
        return list(self.servers)

    def show_servers(self, picked):
        bits = []
        for s in self.servers:
            if s in picked:
                bits.append("1")
            else:
                bits.append("0")
        #d = [s in picked and "1" or "0" for s in self.servers]
        return "".join(bits)

    def dump_usage(self, numfiles, avg_space_per_file):
        print "uploaded", numfiles
        # avg_space_per_file measures expected grid-wide ciphertext per file
        used = list(reversed(sorted([s.used for s in self.servers])))
        # used is actual per-server ciphertext
        usedpf = [1.0*u/numfiles for u in used]
        # usedpf is actual per-server-per-file ciphertext
        #print "min/max usage: %s/%s" % (abbreviate_space(used[-1]),
        #                                abbreviate_space(used[0]))
        avg_usage_per_file = avg_space_per_file/len(self.servers)
        # avg_usage_per_file is expected per-server-per-file ciphertext
        spreadpf = usedpf[0] - usedpf[-1]
        average_usagepf = sum(usedpf) / len(usedpf)
        variance = sum([(u-average_usagepf)**2 for u in usedpf])/(len(usedpf)-1)
        std_deviation = math.sqrt(variance)
        sd_of_total = std_deviation / avg_usage_per_file

        print "min/max/(exp) usage-pf-ps %s/%s/(%s):" % (
            abbreviate_space(usedpf[-1]),
            abbreviate_space(usedpf[0]),
            abbreviate_space(avg_usage_per_file) ),
        print "spread-pf: %s (%.2f%%)" % (
            abbreviate_space(spreadpf), 100.0*spreadpf/avg_usage_per_file),
        #print "average_usage:", abbreviate_space(average_usagepf)
        print "stddev: %s (%.2f%%)" % (abbreviate_space(std_deviation),
                                       100.0*sd_of_total)
        if self.SHOW_MINMAX:
            s2 = sorted(self.servers, key=lambda s: s.used)
            print "least:", s2[0].nodeid
            print "most:", s2[-1].nodeid


class Options(usage.Options):
    optParameters = [
        ("k", "k", 3, "required shares", int),
        ("N", "N", 10, "total shares", int),
        ("servers", None, 100, "number of servers", int),
        ("seed", None, None, "seed to use for creating ring"),
        ("fileseed", None, "blah", "seed to use for creating files"),
        ("permute", "p", 1, "1 to permute, 0 to use flat ring", int),
        ]
    def postOptions(self):
        assert self["seed"]


def do_run(ring, opts):
    avg_space_per_file = avg_filesize * opts["N"] / opts["k"]
    fileseed = opts["fileseed"]
    start = time.time()
    all_servers_have_room = True
    no_files_have_wrapped = True
    for filenum in count(0):
        #used = list(reversed(sorted([s.used for s in ring.servers])))
        #used = [s.used for s in ring.servers]
        #print used
        si = myhash(fileseed+str(filenum)).hexdigest()
        filesize = make_up_a_file_size(si)
        sharesize = filesize / opts["k"]
        if filenum%4000==0 and filenum > 1:
            ring.dump_usage(filenum, avg_space_per_file)
        servers = ring.servers_for_si(si)
        #print ring.show_servers(servers[:opts["N"]])
        remaining_shares = opts["N"]
        index = 0
        server_was_full = False
        file_was_wrapped = False
        remaining_servers = set(servers)
        while remaining_shares:
            if index >= len(servers):
                index = 0
                file_was_wrapped = True
            s = servers[index]
            accepted = s.upload(sharesize)
            if not accepted:
                server_was_full = True
                remaining_servers.discard(s)
                if not remaining_servers:
                    print "-- GRID IS FULL"
                    ring.dump_usage(filenum, avg_space_per_file)
                    return filenum
                index += 1
                continue
            remaining_shares -= 1
            index += 1
        # file is done being uploaded

        if server_was_full and all_servers_have_room:
            all_servers_have_room = False
            print "-- FIRST SERVER FULL"
            ring.dump_usage(filenum, avg_space_per_file)
        if file_was_wrapped and no_files_have_wrapped:
            no_files_have_wrapped = False
            print "-- FIRST FILE WRAPPED"
            ring.dump_usage(filenum, avg_space_per_file)


def do_ring(opts):
    total_capacity = opts["servers"]*SERVER_CAPACITY
    avg_space_per_file = avg_filesize * opts["N"] / opts["k"]
    avg_files = total_capacity / avg_space_per_file
    print "expected number of uploads:", avg_files
    if opts["permute"]:
        print " PERMUTED"
    else:
        print " LINEAR"
    seed = opts["seed"]

    ring = Ring(opts["servers"], seed, opts["permute"])
    num_files = do_run(ring, opts)

def run(opts):
    do_ring(opts)

if __name__ == "__main__":
    opts = Options()
    opts.parseOptions()
    run(opts)
