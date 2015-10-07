#!/usr/bin/env python

# this is a smoke-test using "./bin/tahoe" to:
#
# 1. create an introducer
# 2. create 5 storage nodes
# 3. create 2 client nodes (client0 = alice, client1 = bob)
# 4. Alice creates a magic-folder ("magik:")
# 5. Alice invites Bob
# 6. Bob joins
#
# After that, some basic tests are performed; see the "if True:"
# blocks to turn some on or off. Could benefit from some cleanups
# etc. but this seems useful out of the gate for quick testing.
#
# TO RUN:
# from top-level of your checkout (we use "./bin/tahoe"):
# python src/allmydata/test/check_magicfolder_smoke.py
#
# This will create "./smoke_magicfolder" (which is disposable) and
# contains all the Tahoe basedirs for the introducer, storage nodes,
# clients, and the clients' magic-folders. NOTE that if these
# directories already exist they will NOT be re-created. So kill the
# grid and then "rm -rf smoke_magicfolder" if you want to re-run the
# tests cleanly.
#
# Run the script with a single arg, "kill" to run "tahoe stop" on all
# the nodes.
#
# This will have "tahoe start" -ed all the nodes, so you can continue
# to play around after the script exits.

from __future__ import print_function

import sys
import time
import subprocess
from os.path import join, abspath, curdir, exists
from os import mkdir, listdir, unlink

tahoe_base = abspath(curdir)
data_base = join(tahoe_base, 'smoke_magicfolder')
tahoe_bin = join(tahoe_base, 'bin', 'tahoe')

if not exists(data_base):
    print("Creating", data_base)
    mkdir(data_base)

if not exists(tahoe_bin):
    raise RuntimeError("Can't find 'tahoe' binary at '{}'".format(tahoe_bin))

if 'kill' in sys.argv:
    print("Killing the grid")
    for d in listdir(data_base):
        print("killing", d)
        subprocess.call(
            [
                tahoe_bin, 'stop', join(data_base, d),
            ]
        )
    sys.exit(0)

if not exists(join(data_base, 'introducer')):
    subprocess.check_call(
        [
            tahoe_bin, 'create-introducer', join(data_base, 'introducer'),
        ]
    )
with open(join(data_base, 'introducer', 'tahoe.cfg'), 'w') as f:
    f.write('''
[node]
nickname = introducer0
web.port = 4560
''')
subprocess.check_call(
    [
        tahoe_bin, 'start', join(data_base, 'introducer'),
    ]
)

furl_fname = join(data_base, 'introducer', 'private', 'introducer.furl')
while not exists(furl_fname):
    time.sleep(.1)
furl = open(furl_fname, 'r').read()
print("FURL", furl)

for x in range(5):
    data_dir = join(data_base, 'node%d' % x)
    if not exists(data_dir):
        subprocess.check_call(
            [
                tahoe_bin, 'create-node', data_dir,
            ]
        )
        with open(join(data_dir, 'tahoe.cfg'), 'w') as f:
            f.write('''
[node]
nickname = node{node_id}
web.port =
web.static = public_html
tub.location = localhost:{tub_port}

[client]
# Which services should this client connect to?
introducer.furl = {furl}
shares.needed = 2
shares.happy = 3
shares.total = 4
'''.format(node_id=x, furl=furl, tub_port=(9900 + x)))
    subprocess.check_call(
        [
            tahoe_bin, 'start', data_dir,
        ]
    )



# alice and bob clients
do_invites = False
for x in range(2):
    data_dir = join(data_base, 'client%d' % x)
    magic_dir = join(data_base, 'client%d-magic' % x)
    mkdir(magic_dir)
    if not exists(data_dir):
        do_invites = True
        subprocess.check_call(
            [
                tahoe_bin, 'create-node', data_dir,
            ]
        )
        with open(join(data_dir, 'tahoe.cfg'), 'w') as f:
            f.write('''
[node]
nickname = client{node_id}
web.port = tcp:998{node_id}:interface=localhost
web.static = public_html

[client]
# Which services should this client connect to?
introducer.furl = {furl}
shares.needed = 2
shares.happy = 3
shares.total = 4
'''.format(node_id=x, furl=furl, magic_dir=magic_dir))
    subprocess.check_call(
        [
            tahoe_bin, 'start', data_dir,
        ]
    )

# okay, now we have alice + bob (client0, client1)
# now we have alice create a magic-folder, and invite bob to it

if do_invites:
    data_dir = join(data_base, 'client0')
    # alice/client0 creates her folder, invites bob
    print("Alice creates a magic-folder")
    subprocess.check_call(
        [
            tahoe_bin, 'magic-folder', 'create', '--basedir', data_dir, 'magik:', 'alice',
            join(data_base, 'client0-magic'),
        ]
    )
    print("Alice invites Bob")
    invite = subprocess.check_output(
        [
            tahoe_bin, 'magic-folder', 'invite', '--basedir', data_dir, 'magik:', 'bob',
        ]
    )
    print("  invite:", invite)

    # now we let "bob"/client1 join
    print("Bob joins Alice's magic folder")
    data_dir = join(data_base, 'client1')
    subprocess.check_call(
        [
            tahoe_bin, 'magic-folder', 'join', '--basedir', data_dir, invite,
            join(data_base, 'client1-magic'),
        ]
    )
    print("Bob has joined.")

    print("Restarting alice + bob clients")
    subprocess.check_call(
        [
            tahoe_bin, 'restart', '--basedir', join(data_base, 'client0'),
        ]
    )
    subprocess.check_call(
        [
            tahoe_bin, 'restart', '--basedir', join(data_base, 'client1'),
        ]
    )

if True:
    for x in range(2):
        with open(join(data_base, 'client{}'.format(x), 'private', 'magic_folder_dircap'), 'r') as f:
            print("dircap{}: {}".format(x, f.read().strip()))

if True:
    # alice writes a file; bob should get it
    alice_foo = join(data_base, 'client0-magic', 'first_file')
    bob_foo = join(data_base, 'client1-magic', 'first_file')
    with open(alice_foo, 'w') as f:
        f.write("line one\n")

    print("Waiting for:", bob_foo)
    while True:
        if exists(bob_foo):
            print("  found", bob_foo)
            with open(bob_foo, 'r') as f:
                if f.read() == "line one\n":
                    break
                print("  file contents still mismatched")
        time.sleep(.1)

if True:
    # bob writes a file; alice should get it
    alice_bar = join(data_base, 'client0-magic', 'second_file')
    bob_bar = join(data_base, 'client1-magic', 'second_file')
    with open(bob_bar, 'w') as f:
        f.write("line one\n")

    print("Waiting for:", alice_bar)
    while True:
        if exists(bob_bar):
            print("  found", bob_bar)
            with open(bob_bar, 'r') as f:
                if f.read() == "line one\n":
                    break
                print("  file contents still mismatched")
        time.sleep(.1)

if False:
    # bob deletes alice's "first_file"; alice should also delete it
    alice_foo = join(data_base, 'client0-magic', 'first_file')
    bob_foo = join(data_base, 'client1-magic', 'first_file')
    unlink(bob_foo)

    print("Waiting for '%s' to disappear" % (alice_foo,))
    while True:
        if not exists(alice_foo):
            print("  disappeared", alice_foo)
        time.sleep(.1)

# XXX test .backup (delete a file)

# port david's clock.advance stuff
# fix clock.advance()
# subdirectory
# file deletes
# conflicts
