#! /usr/bin/python

import random, math, os, re
from twisted.python import usage

class Args(usage.Options):
    optParameters = [
        ["mode", "m", "alpha", "validation scheme"],
        ["arity", "k", 2, "k (airty) for hash tree"],
        ]
    def opt_arity(self, option):
        self['arity'] = int(option)
    def parseArgs(self, *args):
        if len(args) > 0:
            self['mode'] = args[0]


def charttest():
    import gdchart
    sizes = [random.randrange(10, 20) for i in range(10)]
    x = gdchart.Line()
    x.width = 250
    x.height = 250
    x.xtitle = "sample"
    x.ytitle = "size"
    x.title = "Example Graph"
    #x.ext_color = [ "white", "yellow", "red", "blue", "green"]
    x.setData(sizes)
    #x.setLabels(["Mon", "Tue", "Wed", "Thu", "Fri"])
    x.draw("simple.png")

KiB=1024
MiB=1024*KiB
GiB=1024*MiB
TiB=1024*GiB
PiB=1024*TiB

class Sizes:
    def __init__(self, mode, file_size, arity=2):
        MAX_SEGSIZE = 2*MiB
        self.mode = mode
        self.file_size = file_size
        self.seg_size = seg_size = 1.0 * min(MAX_SEGSIZE, file_size)
        self.num_segs = num_segs = math.ceil(file_size / seg_size)
        self.num_subblocks = num_subblocks = num_segs

        self.num_blocks = num_blocks = 100
        self.blocks_needed = blocks_needed = 25

        self.subblock_size = subblock_size = seg_size / blocks_needed
        self.block_size = block_size = subblock_size * num_subblocks

        # none of this includes the block-level hash chain yet, since that is
        # only a function of the number of blocks. All overhead numbers
        # assume that the block-level hash chain has already been sent,
        # including the root of the subblock-level hash tree.

        if mode == "alpha":
            # no hash tree at all
            self.subblock_arity = 0
            self.subblock_tree_depth = 0
            self.subblock_overhead = 0
            self.bytes_until_some_data = 20 + block_size
            self.block_storage_overhead = 0
            self.block_transmission_overhead = 0

        elif mode == "beta":
            # k=num_subblocks, d=1
            # each subblock has a 20-byte hash
            self.subblock_arity = num_subblocks
            self.subblock_tree_depth = 1
            self.subblock_overhead = 20
            # the block has a list of hashes, one for each subblock
            self.block_storage_overhead = (self.subblock_overhead *
                                           num_subblocks)
            # we can get away with not sending the hash of the block that
            # we're sending in full, once
            self.block_transmission_overhead = self.block_storage_overhead - 20
            # we must get the whole list (so it can be validated) before
            # any data can be validated
            self.bytes_until_some_data = (self.block_transmission_overhead +
                                          subblock_size)

        elif mode == "gamma":
            self.subblock_arity = k = arity
            d = math.ceil(math.log(num_subblocks, k))
            self.subblock_tree_depth = d
            num_leaves = k ** d
            # to make things easier, we make the pessimistic assumption that
            # we have to store hashes for all the empty places in the tree
            # (when the number of blocks is not an exact exponent of k)
            self.subblock_overhead = 20
            # the subblock hashes are organized into a k-ary tree, which
            # means storing (and eventually transmitting) more hashes. This
            # count includes all the low-level block hashes and the root.
            hash_nodes = (num_leaves*k - 1) / (k - 1)
            #print "hash_depth", d
            #print "num_leaves", num_leaves
            #print "hash_nodes", hash_nodes
            # the storage overhead is this
            self.block_storage_overhead = 20 * (hash_nodes - 1)
            # the transmission overhead is smaller: if we actually transmit
            # every subblock, we don't have to transmit 1/k of the
            # lowest-level subblock hashes, and we don't have to transmit the
            # root because it was already sent with the block-level hash tree
            self.block_transmission_overhead = 20 * (hash_nodes
                                                     - 1 # the root
                                                     - num_leaves / k)
            # we must get a full sibling hash chain before we can validate
            # any data
            sibling_length = d * (k-1)
            self.bytes_until_some_data = 20 * sibling_length + subblock_size
            
            

        else:
            raise RuntimeError("unknown mode '%s" % mode)

        self.storage_overhead = self.block_storage_overhead * num_blocks
        self.storage_overhead_percentage = 100.0 * self.storage_overhead / file_size

    def dump(self):
        for k in ("mode", "file_size", "seg_size",
                  "num_segs", "num_subblocks", "num_blocks", "blocks_needed",
                  "subblock_size", "block_size",
                  "subblock_arity", "subblock_tree_depth",
                  "subblock_overhead",
                  "block_storage_overhead", "block_transmission_overhead",
                  "storage_overhead", "storage_overhead_percentage",
                  "bytes_until_some_data"):
            print k, getattr(self, k)

def fmt(num, trim=False):
    if num < KiB:
        #s = str(num) + "#"
        s = "%.2f#" % num
    elif num < MiB:
        s = "%.2fk" % (num / KiB)
    elif num < GiB:
        s = "%.2fM" % (num / MiB)
    elif num < TiB:
        s = "%.2fG" % (num / GiB)
    elif num < PiB:
        s = "%.2fT" % (num / TiB)
    else:
        s = "big"
    if trim:
        s = re.sub(r'(\.0+)([kMGT#])',
                   lambda m: m.group(2),
                   s)
    else:
        s = re.sub(r'(\.0+)([kMGT#])',
                   lambda m: (" "*len(m.group(1))+m.group(2)),
                   s)
    if s.endswith("#"):
        s = s[:-1] + " "
    return s

def text():
    opts = Args()
    opts.parseOptions()
    mode = opts["mode"]
    arity = opts["arity"]
    #      0123456789012345678901234567890123456789012345678901234567890123456
    print "mode=%s" % mode, " arity=%d" % arity
    print "                    storage    storage"
    print "Size     blocksize  overhead   overhead     k  d  alacrity"
    print "                    (bytes)      (%)"
    print "-------  -------    --------   --------  ---- --  --------"
    #sizes = [2 ** i for i in range(7, 41)]
    radix = math.sqrt(10); expstep = 2
    radix = 2; expstep = 2
    #radix = 10; expstep = 1
    maxexp = int(math.ceil(math.log(1e12, radix)))+2
    sizes = [radix ** i for i in range(2,maxexp,expstep)]
    for file_size in sizes:
        s = Sizes(mode, file_size, arity)
        out = ""
        out += "%7s  " % fmt(file_size, trim=True)
        out += "%7s    " % fmt(s.block_size)
        out += "%8s" % fmt(s.storage_overhead)
        out += "%10.2f  " % s.storage_overhead_percentage
        out += " %4d" % int(s.subblock_arity)
        out += " %2d" % int(s.subblock_tree_depth)
        out += " %8s" % fmt(s.bytes_until_some_data)
        print out


def graph():
    # doesn't work yet
    import Gnuplot
    opts = Args()
    opts.parseOptions()
    mode = opts["mode"]
    arity = opts["arity"]
    g = Gnuplot.Gnuplot(debug=1)
    g.title("overhead / alacrity tradeoffs")
    g.xlabel("file size")
    g.ylabel("stuff")
    sizes = [2 ** i for i in range(7, 32)]
    series = {"overhead": {}, "alacrity": {}}
    for file_size in sizes:
        s = Sizes(mode, file_size, arity)
        series["overhead"][file_size] = s.storage_overhead_percentage
        series["alacrity"][file_size] = s.bytes_until_some_data
    g.plot([ (fs, series["overhead"][fs])
             for fs in sizes ])
    raw_input("press return")


if __name__ == '__main__':
    text()
    #graph()
