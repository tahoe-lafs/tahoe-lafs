#!/usr/bin/env python


import random

class Server:
    def __init__(self):
        self.si = random.randrange(0, 2**31)
        self.used = 0
        self.max = 2**40
        self.full_at_tick = None

    def __repr__(self):
        if self.full_at_tick is not None:
            return "<%s %s full at %d>" % (self.__class__.__name__, self.si, self.full_at_tick)
        else:
            return "<%s %s>" % (self.__class__.__name__, self.si)

SERVERS = 40
K = 3
N = 10

def make_up_a_file_size():
    return (2 ** random.randrange(8, 31))

def go(permutedpeerlist):
    servers = [ Server() for x in range(SERVERS) ]
    servers.sort(cmp=lambda x,y: cmp(x.si, y.si))

    tick = 0
    fullservers = 0
    while True:
        nextsharesize = make_up_a_file_size() / K
        if permutedpeerlist:
            random.shuffle(servers)
        else:
            # rotate a random number
            rot = random.randrange(0, len(servers))
            servers = servers[rot:] + servers[:rot]

        i = 0
        sharestoput = N
        while sharestoput:
            server = servers[i]
            if server.used + nextsharesize < server.max:
                server.used += nextsharesize
                sharestoput -= 1
            else:
                if server.full_at_tick is None:
                    server.full_at_tick = tick
                    fullservers += 1
                    if fullservers == len(servers):
                        # print "Couldn't place share -- all servers full.  Stopping."
                        return servers

            i = (i + 1) % len(servers)

        tick += 1

def div_ceil(n, d):
    """
    The smallest integer k such that k*d >= n.
    """
    return (n/d) + (n%d != 0)

DESIRED_COLUMNS = 70

START_FILES = 137000
STOP_FILES = 144000

def test(permutedpeerlist, iters):
    # The i'th element of the filledat list is how many servers got full when the i'th file was uploaded.
    filledat = []
    for test in range(iters):
        servers = go(permutedpeerlist)
        for server in servers:
            fidx = server.full_at_tick
            filledat.extend([0]*(fidx-len(filledat)+1))
            filledat[fidx] += 1

    startfiles = 0
    while filledat[startfiles] == 0:
        startfiles += 1
    filespercolumn = div_ceil(len(filledat) - startfiles, (DESIRED_COLUMNS - 3))

    # to make comparisons between runs line up:
    startfiles = START_FILES
    filespercolumn = div_ceil(STOP_FILES - startfiles, (DESIRED_COLUMNS - 3))

    # The i'th element of the compressedfilledat list is how many servers got full when the filespercolumn files starting at startfiles + i were uploaded.
    compressedfilledat = []
    idx = startfiles
    while idx < len(filledat):
        compressedfilledat.append(0)
        for i in range(filespercolumn):
            compressedfilledat[-1] += filledat[idx]
            idx += 1
            if idx >= len(filledat):
                break

    # The i'th element of the fullat list is how many servers were full by the tick numbered startfiles + i * filespercolumn (on average).
    fullat = [0] * len(compressedfilledat)
    for idx, num in enumerate(compressedfilledat):
        for fidx in range(idx, len(fullat)):
            fullat[fidx] += num

    for idx in range(len(fullat)):
        fullat[idx]  = fullat[idx] / float(iters)

    # Now print it out as an ascii art graph.
    import sys
    for serversfull in range(40, 0, -1):
        sys.stdout.write("%2d " % serversfull)
        for numfull in fullat:
            if int(numfull) == serversfull:
                sys.stdout.write("*")
            else:
                sys.stdout.write(" ")
        sys.stdout.write("\n")

    sys.stdout.write(" ^-- servers full\n")
    idx = 0
    while idx < len(fullat):
        nextmark  = "%d--^ " % (startfiles + idx * filespercolumn)
        sys.stdout.write(nextmark)
        idx += len(nextmark)

    sys.stdout.write("\nfiles uploaded --> \n")



if __name__ == "__main__":
    import sys
    iters = 16
    for arg in sys.argv:
        if arg.startswith("--iters="):
            iters = int(arg[8:])
    if "--permute" in sys.argv:
        print "doing permuted peerlist, iterations: %d" % iters
        test(True, iters)
    else:
        print "doing simple ring, iterations: %d" % iters
        test(False, iters)
