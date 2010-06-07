#! /usr/bin/env python

import sha as shamodule
import os, random

from pkg_resources import require
require('PyRRD')
from pyrrd import graph
from pyrrd.rrd import DataSource, RRD, RRA


def sha(s):
    return shamodule.new(s).digest()

def randomid():
    return os.urandom(20)

class Node:
    def __init__(self, nid, introducer, simulator):
        self.nid = nid
        self.introducer = introducer
        self.simulator = simulator
        self.shares = {}
        self.capacity = random.randrange(1000)
        self.utilization = 0
        self.files = []

    def permute_peers(self, fileid):
        permuted = [(sha(fileid+n.nid),n)
                    for n in self.introducer.get_all_nodes()]
        permuted.sort()
        return permuted

    def publish_file(self, fileid, size, numshares=100):
        sharesize = 4 * size / numshares
        permuted = self.permute_peers(fileid)
        last_givento = None
        tried = 0
        givento = []
        while numshares and permuted:
            pid,node = permuted.pop(0)
            tried += 1
            last_givento = pid
            if node.accept_share(fileid, sharesize):
                givento.append((pid,node))
                numshares -= 1
        if numshares:
            # couldn't push, should delete
            for pid,node in givento:
                node.delete_share(fileid)
            return False
        self.files.append((fileid, numshares))
        self.introducer.please_preserve(fileid, size, tried, last_givento)
        return (True, tried)

    def accept_share(self, fileid, sharesize):
        accept = False
        if self.utilization < self.capacity:
            # we have room! yay!
            self.shares[fileid] = sharesize
            self.utilization += sharesize
            return True
        if self.decide(sharesize):
            # we don't, but we'll make room
            self.make_space(sharesize)
            self.shares[fileid] = sharesize
            self.utilization += sharesize
            return True
        else:
            # we're full, try elsewhere
            return False

    def decide(self, sharesize):
        if sharesize > self.capacity:
            return False
        return False
        return random.random() > 0.5

    def make_space(self, sharesize):
        assert sharesize <= self.capacity
        while self.capacity - self.utilization < sharesize:
            victim = random.choice(self.shares.keys())
            self.simulator.lost_data(self.shares[victim])
            self.delete_share(victim)

    def delete_share(self, fileid):
        if fileid in self.shares:
            self.utilization -= self.shares[fileid]
            del self.shares[fileid]
            return True
        return False

    def retrieve_file(self):
        if not self.files:
            return
        fileid,numshares = random.choice(self.files)
        needed = numshares / 4
        peers = []
        for pid,node in self.permute_peers(fileid):
            if random.random() > self.simulator.P_NODEAVAIL:
                continue # node isn't available right now
            if node.has_share(fileid):
                peers.append(node)
            if len(peers) >= needed:
                return True
        return False

    def delete_file(self):
        if not self.files:
            return False
        which = random.choice(self.files)
        self.files.remove(which)
        fileid,numshares = which
        self.introducer.delete(fileid)
        return True

class Introducer:
    def __init__(self, simulator):
        self.living_files = {}
        self.utilization = 0 # total size of all active files
        self.simulator = simulator
        self.simulator.stamp_utilization(self.utilization)

    def get_all_nodes(self):
        return self.all_nodes

    def please_preserve(self, fileid, size, tried, last_givento):
        self.living_files[fileid] = (size, tried, last_givento)
        self.utilization += size
        self.simulator.stamp_utilization(self.utilization)

    def please_delete(self, fileid):
        self.delete(fileid)

    def permute_peers(self, fileid):
        permuted = [(sha(fileid+n.nid),n)
                    for n in self.get_all_nodes()]
        permuted.sort()
        return permuted

    def delete(self, fileid):
        permuted = self.permute_peers(fileid)
        size, tried, last_givento = self.living_files[fileid]
        pid = ""
        while tried and pid < last_givento:
            pid,node = permuted.pop(0)
            had_it = node.delete_share(fileid)
            if had_it:
                tried -= 1
        self.utilization -= size
        self.simulator.stamp_utilization(self.utilization)
        del self.living_files[fileid]

class Simulator:
    NUM_NODES = 1000
    EVENTS = ["ADDFILE", "DELFILE", "ADDNODE", "DELNODE"]
    RATE_ADDFILE = 1.0 / 10
    RATE_DELFILE = 1.0 / 20
    RATE_ADDNODE = 1.0 / 3000
    RATE_DELNODE = 1.0 / 4000
    P_NODEAVAIL = 1.0

    def __init__(self):
        self.time = 1164783600 # small numbers of seconds since the epoch confuse rrdtool
        self.prevstamptime = int(self.time)

        ds = DataSource(ds_name='utilizationds', ds_type='GAUGE', heartbeat=1)
        rra = RRA(cf='AVERAGE', xff=0.1, steps=1, rows=1200)
        self.rrd = RRD("/tmp/utilization.rrd", ds=[ds], rra=[rra], start=self.time)
        self.rrd.create()

        self.introducer = q = Introducer(self)
        self.all_nodes = [Node(randomid(), q, self)
                          for i in range(self.NUM_NODES)]
        q.all_nodes = self.all_nodes
        self.next = []
        self.schedule_events()
        self.verbose = False

        self.added_files = 0
        self.added_data = 0
        self.deleted_files = 0
        self.published_files = []
        self.failed_files = 0
        self.lost_data_bytes = 0 # bytes deleted to make room for new shares

    def stamp_utilization(self, utilization):
        if int(self.time) > (self.prevstamptime+1):
            self.rrd.bufferValue(self.time, utilization)
            self.prevstamptime = int(self.time)

    def write_graph(self):
        self.rrd.update()
        self.rrd = None
        import gc
        gc.collect()

        def1 = graph.DataDefinition(vname="a", rrdfile='/tmp/utilization.rrd', ds_name='utilizationds')
        area1 = graph.Area(value="a", color="#990033", legend='utilizationlegend')
        g = graph.Graph('/tmp/utilization.png', imgformat='PNG', width=540, height=100, vertical_label='utilizationverticallabel', title='utilizationtitle', lower_limit=0)
        g.data.append(def1)
        g.data.append(area1)
        g.write()

    def add_file(self):
        size = random.randrange(1000)
        n = random.choice(self.all_nodes)
        if self.verbose:
            print "add_file(size=%d, from node %s)" % (size, n)
        fileid = randomid()
        able = n.publish_file(fileid, size)
        if able:
            able, tried = able
            self.added_files += 1
            self.added_data += size
            self.published_files.append(tried)
        else:
            self.failed_files += 1

    def lost_data(self, size):
        self.lost_data_bytes += size

    def delete_file(self):
        all_nodes = self.all_nodes[:]
        random.shuffle(all_nodes)
        for n in all_nodes:
            if n.delete_file():
                self.deleted_files += 1
                return
        print "no files to delete"

    def _add_event(self, etype):
        rate = getattr(self, "RATE_" + etype)
        next = self.time + random.expovariate(rate)
        self.next.append((next, etype))
        self.next.sort()

    def schedule_events(self):
        types = set([e[1] for e in self.next])
        for etype in self.EVENTS:
            if not etype in types:
                self._add_event(etype)

    def do_event(self):
        time, etype = self.next.pop(0)
        assert time > self.time
        current_time = self.time
        self.time = time
        self._add_event(etype)
        if etype == "ADDFILE":
            self.add_file()
        elif etype == "DELFILE":
            self.delete_file()
        elif etype == "ADDNODE":
            pass
            #self.add_node()
        elif etype == "DELNODE":
            #self.del_node()
            pass
        # self.print_stats(current_time, etype)

    def print_stats_header(self):
        print "time:  added   failed   lost  avg_tried"

    def print_stats(self, time, etype):
        if not self.published_files:
            avg_tried = "NONE"
        else:
            avg_tried = sum(self.published_files) / len(self.published_files)
        print time, etype, self.added_data, self.failed_files, self.lost_data_bytes, avg_tried, len(self.introducer.living_files), self.introducer.utilization

global s
s = None

def main():
#    rrdtool.create("foo.rrd",
#                   "--step 10",
#                   "DS:files-added:DERIVE::0:1000",
#                   "RRA:AVERAGE:1:1:1200",
#                   )
    global s
    s = Simulator()
    # s.print_stats_header()
    for i in range(1000):
        s.do_event()
    print "%d files added, %d files deleted" % (s.added_files, s.deleted_files)
    return s

if __name__ == '__main__':
    main()
    

