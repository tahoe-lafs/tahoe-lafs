#!/usr/bin/env python

# this is a smoke-test using "./bin/tahoe" to:
#
# 1. create an introducer
# 2. create 5 storage nodes
# 3. create 2 client nodes (alice, bob)
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
import shutil
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
    time.sleep(1)
furl = open(furl_fname, 'r').read()
print("FURL", furl)

for x in range(5):
    data_dir = join(data_base, 'node%d' % x)
    if not exists(data_dir):
        subprocess.check_call(
            [
                tahoe_bin, 'create-node',
                '--nickname', 'node{}'.format(x),
                '--introducer', furl,
                data_dir,
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
node_id = 0
for name in ['alice', 'bob']:
    data_dir = join(data_base, name)
    magic_dir = join(data_base, '{}-magic'.format(name))
    mkdir(magic_dir)
    if not exists(data_dir):
        do_invites = True
        subprocess.check_call(
            [
                tahoe_bin, 'create-node',
                '--no-storage',
                '--nickname', name,
                '--introducer', furl,
                data_dir,
            ]
        )
        with open(join(data_dir, 'tahoe.cfg'), 'w') as f:
            f.write('''
[node]
nickname = {name}
web.port = tcp:998{node_id}:interface=localhost
web.static = public_html

[client]
# Which services should this client connect to?
introducer.furl = {furl}
shares.needed = 2
shares.happy = 3
shares.total = 4
'''.format(name=name, node_id=node_id, furl=furl, magic_dir=magic_dir))
    subprocess.check_call(
        [
            tahoe_bin, 'start', data_dir,
        ]
    )
    node_id += 1

# okay, now we have alice + bob (alice, bob)
# now we have alice create a magic-folder, and invite bob to it

if do_invites:
    data_dir = join(data_base, 'alice')
    # alice creates her folder, invites bob
    print("Alice creates a magic-folder")
    subprocess.check_call(
        [
            tahoe_bin, 'magic-folder', 'create', '--basedir', data_dir, 'magik:', 'alice',
            join(data_base, 'alice-magic'),
        ]
    )
    print("Alice invites Bob")
    invite = subprocess.check_output(
        [
            tahoe_bin, 'magic-folder', 'invite', '--basedir', data_dir, 'magik:', 'bob',
        ]
    )
    print("  invite:", invite)

    # now we let "bob"/bob join
    print("Bob joins Alice's magic folder")
    data_dir = join(data_base, 'bob')
    subprocess.check_call(
        [
            tahoe_bin, 'magic-folder', 'join', '--basedir', data_dir, invite,
            join(data_base, 'bob-magic'),
        ]
    )
    print("Bob has joined.")

    print("Restarting alice + bob clients")
    subprocess.check_call(
        [
            tahoe_bin, 'restart', '--basedir', join(data_base, 'alice'),
        ]
    )
    subprocess.check_call(
        [
            tahoe_bin, 'restart', '--basedir', join(data_base, 'bob'),
        ]
    )

if True:
    for name in ['alice', 'bob']:
        with open(join(data_base, name, 'private', 'magic_folder_dircap'), 'r') as f:
            print("dircap {}: {}".format(name, f.read().strip()))

# give storage nodes a chance to connect properly? I'm not entirely
# sure what's up here, but I get "UnrecoverableFileError" on the
# first_file upload from Alice "very often" otherwise
print("waiting 3 seconds")
time.sleep(3)

if True:
    # alice writes a file; bob should get it
    alice_foo = join(data_base, 'alice-magic', 'first_file')
    bob_foo = join(data_base, 'bob-magic', 'first_file')
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
        time.sleep(1)

if True:
    # bob writes a file; alice should get it
    alice_bar = join(data_base, 'alice-magic', 'second_file')
    bob_bar = join(data_base, 'bob-magic', 'second_file')
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
        time.sleep(1)

if True:
    # alice deletes 'first_file'
    alice_foo = join(data_base, 'alice-magic', 'first_file')
    bob_foo = join(data_base, 'bob-magic', 'first_file')
    unlink(alice_foo)

    print("Waiting for '%s' to disappear" % (bob_foo,))
    while True:
        if not exists(bob_foo):
            print("  disappeared", bob_foo)
            break
        time.sleep(1)

    # XXX this doesn't work; shouldn't a .tmp file appear on bob's side?
    bob_tmp = bob_foo + '.tmp'
    print("Waiting for '%s' to appear" % (bob_tmp,))
    while True:
        if exists(bob_tmp):
            print("  appeared", bob_tmp)
            break
        time.sleep(1)

if True:
    # bob writes new content to 'second_file'; alice should get it
    # get it.
    alice_foo = join(data_base, 'alice-magic', 'second_file')
    bob_foo = join(data_base, 'bob-magic', 'second_file')
    gold_content = "line one\nsecond line\n"

    with open(bob_foo, 'w') as f:
        f.write(gold_content)

    print("Waiting for:", alice_foo)
    while True:
        if exists(alice_foo):
            print("  found", alice_foo)
            with open(alice_foo, 'r') as f:
                content = f.read()
                if content == gold_content:
                    break
                print("  file contents still mismatched:\n")
                print(content)
        time.sleep(1)

if True:
    # bob creates a sub-directory and adds a file to it
    alice_dir = join(data_base, 'alice-magic', 'subdir')
    bob_dir = join(data_base, 'alice-magic', 'subdir')
    gold_content = 'a file in a subdirectory\n'

    mkdir(bob_dir)
    with open(join(bob_dir, 'subfile'), 'w') as f:
        f.write(gold_content)

    print("Waiting for Bob's subdir '%s' to appear" % (bob_dir,))
    while True:
        if exists(bob_dir):
            print("  found subdir")
            if exists(join(bob_dir, 'subfile')):
                print("  found file")
                with open(join(bob_dir, 'subfile'), 'r') as f:
                    if f.read() == gold_content:
                        print("  contents match")
                        break
        time.sleep(0.1)

if True:
    # bob deletes the whole subdir
    alice_dir = join(data_base, 'alice-magic', 'subdir')
    bob_dir = join(data_base, 'alice-magic', 'subdir')
    shutil.rmtree(bob_dir)

    print("Waiting for Alice's subdir '%s' to disappear" % (alice_dir,))
    while True:
        if not exists(alice_dir):
            print("  it's gone")
            break
        time.sleep(0.1)

# XXX restore the file not working (but, unit-tests work; what's wrong with them?)
# NOTE: only not-works if it's alice restoring the file!
if True:
    # restore 'first_file' but with different contents
    print("re-writing 'first_file'")
    assert not exists(join(data_base, 'bob-magic', 'first_file'))
    assert not exists(join(data_base, 'alice-magic', 'first_file'))
    alice_foo = join(data_base, 'alice-magic', 'first_file')
    bob_foo = join(data_base, 'bob-magic', 'first_file')
    if True:
        # if we don't swap around, it works fine
        alice_foo, bob_foo = bob_foo, alice_foo
    gold_content = "see it again for the first time\n"

    with open(bob_foo, 'w') as f:
        f.write(gold_content)

    print("Waiting for:", alice_foo)
    while True:
        if exists(alice_foo):
            print("  found", alice_foo)
            with open(alice_foo, 'r') as f:
                content = f.read()
                if content == gold_content:
                    break
                print("  file contents still mismatched: %d bytes:\n" % (len(content),))
                print(content)
        else:
            print("   {} not there yet".format(alice_foo))
        time.sleep(1)

# XXX test .backup (delete a file)

# port david's clock.advance stuff
# fix clock.advance()
# subdirectory
# file deletes
# conflicts
