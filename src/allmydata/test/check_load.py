#! /usr/bin/python

"""
this is a load-generating client program. It does all of its work through a
given tahoe node (specified by URL), and performs random reads and writes
to the target.

Run this in a directory with the following files:
 server-URLs : a list of tahoe node URLs (one per line). Each operation
               will use a randomly-selected server.
 root.cap: (string) the top-level directory rwcap to use
 delay: (float) seconds to delay between operations
 operation-mix: "R/W": two ints, relative frequency of read and write ops
 #size:?

Set argv[1] to a per-client stats-NN.out file. This will will be updated with
running totals of bytes-per-second and operations-per-second. The stats from
multiple clients can be totalled together and averaged over time to compute
the traffic being accepted by the grid.

Each time a 'read' operation is performed, the client will begin at the root
and randomly choose a child. If the child is a directory, the client will
recurse. If the child is a file, the client will read the contents of the
file.

Each time a 'write' operation is performed, the client will generate a target
filename (a random string). 90% of the time, the file will be written into
the same directory that was used last time (starting at the root). 10% of the
time, a new directory is created by assembling 1 to 5 pathnames chosen at
random. The client then writes a certain number of zero bytes to this file.
The filesize is determined with something like a power-law distribution, with
a mean of 10kB and a max of 100MB, so filesize=min(int(1.0/random(.0002)),1e8)


"""

import os, sys, httplib, binascii
import urllib, simplejson, random, time, urlparse

stats_out = sys.argv[1]

server_urls = []
for url in open("server-URLs", "r").readlines():
    url = url.strip()
    if url:
        server_urls.append(url)
root = open("root.cap", "r").read().strip()
delay = float(open("delay", "r").read().strip())
readfreq, writefreq = (
    [int(x) for x in open("operation-mix", "r").read().strip().split("/")])



def listdir(nodeurl, root, vdrive_pathname):
    if nodeurl[-1] != "/":
        nodeurl += "/"
    url = nodeurl + "uri/%s/" % urllib.quote(root)
    if vdrive_pathname:
        url += urllib.quote(vdrive_pathname)
    url += "?t=json"
    data = urllib.urlopen(url).read()
    try:
        parsed = simplejson.loads(data)
    except ValueError:
        print "URL was", url
        print "DATA was", data
        raise
    nodetype, d = parsed
    assert nodetype == "dirnode"
    return d['children']


def choose_random_descendant(server_url, root, pathname=""):
    children = listdir(server_url, root, pathname)
    name = random.choice(children.keys())
    child = children[name]
    if pathname:
        new_pathname = pathname + "/" + name
    else:
        new_pathname = name
    if child[0] == "filenode":
        return new_pathname
    return choose_random_descendant(server_url, root, new_pathname)

def read_and_discard(nodeurl, root, pathname):
    if nodeurl[-1] != "/":
        nodeurl += "/"
    url = nodeurl + "uri/%s/" % urllib.quote(root)
    if pathname:
        url += urllib.quote(pathname)
    f = urllib.urlopen(url)
    while True:
        data = f.read(4096)
        if not data:
            break

directories = [
    "dreamland/disengaging/hucksters",
    "dreamland/disengaging/klondikes",
    "dreamland/disengaging/neatly",
    "dreamland/cottages/richmond",
    "dreamland/cottages/perhaps",
    "dreamland/cottages/spies",
    "dreamland/finder/diversion",
    "dreamland/finder/cigarette",
    "dreamland/finder/album",
    "hazing/licences/comedian",
    "hazing/licences/goat",
    "hazing/licences/shopkeeper",
    "hazing/regiment/frigate",
    "hazing/regiment/quackery",
    "hazing/regiment/centerpiece",
    "hazing/disassociate/mob",
    "hazing/disassociate/nihilistic",
    "hazing/disassociate/bilbo",
    ]

def create_random_directory():
    d = random.choice(directories)
    pieces = d.split("/")
    numsegs = random.randint(1, len(pieces))
    return "/".join(pieces[0:numsegs])

def generate_filename():
    fn = binascii.hexlify(os.urandom(4))
    return fn

def choose_size():
    mean = 10e3
    size = random.expovariate(1.0 / mean)
    return int(min(size, 100e6))

# copied from twisted/web/client.py
def parse_url(url, defaultPort=None):
    url = url.strip()
    parsed = urlparse.urlparse(url)
    scheme = parsed[0]
    path = urlparse.urlunparse(('','')+parsed[2:])
    if defaultPort is None:
        if scheme == 'https':
            defaultPort = 443
        else:
            defaultPort = 80
    host, port = parsed[1], defaultPort
    if ':' in host:
        host, port = host.split(':')
        port = int(port)
    if path == "":
        path = "/"
    return scheme, host, port, path

def generate_and_put(nodeurl, root, vdrive_fname, size):
    if nodeurl[-1] != "/":
        nodeurl += "/"
    url = nodeurl + "uri/%s/" % urllib.quote(root)
    url += urllib.quote(vdrive_fname)

    scheme, host, port, path = parse_url(url)
    if scheme == "http":
        c = httplib.HTTPConnection(host, port)
    elif scheme == "https":
        c = httplib.HTTPSConnection(host, port)
    else:
        raise ValueError("unknown scheme '%s', need http or https" % scheme)
    c.putrequest("PUT", path)
    c.putheader("Hostname", host)
    c.putheader("User-Agent", "tahoe-check-load")
    c.putheader("Connection", "close")
    c.putheader("Content-Length", "%d" % size)
    c.endheaders()
    while size:
        chunksize = min(size, 4096)
        size -= chunksize
        c.send("\x00" * chunksize)
    return c.getresponse()


current_writedir = ""

while True:
    time.sleep(delay)
    if random.uniform(0, readfreq+writefreq) > readfreq:
        op = "read"
    else:
        op = "write"
    print "OP:", op
    server = random.choice(server_urls)
    if op == "read":
        pathname = choose_random_descendant(server, root)
        print "  reading", pathname
        read_and_discard(server, root, pathname)
    elif op == "write":
        if random.uniform(0, 100) < 10:
            current_writedir = create_random_directory()
        filename = generate_filename()
        if current_writedir:
            pathname = current_writedir + "/" + filename
        else:
            pathname = filename
        print "  writing", pathname
        size = choose_size()
        print "   size", size
        generate_and_put(server, root, pathname, size)

