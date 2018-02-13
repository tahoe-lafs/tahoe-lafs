import sys
import time
import shutil
from os import mkdir, unlink, listdir, utime
from os.path import join, exists, getmtime

import util

import pytest


# tests converted from check_magicfolder_smoke.py
# see "conftest.py" for the fixtures (e.g. "magic_folder")


def test_alice_writes_bob_receives(magic_folder):
    alice_dir, bob_dir = magic_folder

    with open(join(alice_dir, "first_file"), "w") as f:
        f.write("alice wrote this")

    util.await_file_contents(join(bob_dir, "first_file"), "alice wrote this")
    return


def test_alice_writes_bob_receives_old_timestamp(magic_folder):
    alice_dir, bob_dir = magic_folder
    fname = join(alice_dir, "ts_file")
    ts = time.time() - (60 * 60 * 36)  # 36 hours ago

    with open(fname, "w") as f:
        f.write("alice wrote this")
    utime(fname, (time.time(), ts))

    fname = join(bob_dir, "ts_file")
    util.await_file_contents(fname, "alice wrote this")
    # make sure the timestamp is correct
    assert int(getmtime(fname)) == int(ts)
    return


def test_bob_writes_alice_receives(magic_folder):
    alice_dir, bob_dir = magic_folder

    with open(join(bob_dir, "second_file"), "w") as f:
        f.write("bob wrote this")

    util.await_file_contents(join(alice_dir, "second_file"), "bob wrote this")
    return


def test_alice_deletes(magic_folder):
    # alice writes a file, waits for bob to get it and then deletes it.
    alice_dir, bob_dir = magic_folder

    with open(join(alice_dir, "delfile"), "w") as f:
        f.write("alice wrote this")

    util.await_file_contents(join(bob_dir, "delfile"), "alice wrote this")

    # bob has the file; now alices deletes it
    unlink(join(alice_dir, "delfile"))

    # bob should remove his copy, but preserve a backup
    util.await_file_vanishes(join(bob_dir, "delfile"))
    util.await_file_contents(join(bob_dir, "delfile.backup"), "alice wrote this")
    return


def test_alice_creates_bob_edits(magic_folder):
    alice_dir, bob_dir = magic_folder

    # alice writes a file
    with open(join(alice_dir, "editfile"), "w") as f:
        f.write("alice wrote this")

    util.await_file_contents(join(bob_dir, "editfile"), "alice wrote this")

    # now bob edits it
    with open(join(bob_dir, "editfile"), "w") as f:
        f.write("bob says foo")

    util.await_file_contents(join(alice_dir, "editfile"), "bob says foo")


def test_bob_creates_sub_directory(magic_folder):
    alice_dir, bob_dir = magic_folder

    # bob makes a sub-dir, with a file in it
    mkdir(join(bob_dir, "subdir"))
    with open(join(bob_dir, "subdir", "a_file"), "w") as f:
        f.write("bob wuz here")

    # alice gets it
    util.await_file_contents(join(alice_dir, "subdir", "a_file"), "bob wuz here")

    # now bob deletes it again
    shutil.rmtree(join(bob_dir, "subdir"))

    # alice should delete it as well
    util.await_file_vanishes(join(alice_dir, "subdir", "a_file"))
    # i *think* it's by design that the subdir won't disappear,
    # because a "a_file.backup" should appear...
    util.await_file_contents(join(alice_dir, "subdir", "a_file.backup"), "bob wuz here")


def test_bob_creates_alice_deletes_bob_restores(magic_folder):
    alice_dir, bob_dir = magic_folder

    # bob creates a file
    with open(join(bob_dir, "boom"), "w") as f:
        f.write("bob wrote this")

    util.await_file_contents(
        join(alice_dir, "boom"),
        "bob wrote this"
    )

    # alice deletes it (so bob should as well
    unlink(join(alice_dir, "boom"))
    util.await_file_vanishes(join(bob_dir, "boom"))

    # bob restore it, with new contents
    with open(join(bob_dir, "boom"), "w") as f:
        f.write("bob wrote this again, because reasons")

    # XXX double-check this behavior is correct!

    # alice sees bob's update, but marks it as a conflict (because
    # .. she previously deleted it? does that really make sense)

    util.await_file_contents(
        join(alice_dir, "boom"),
        "bob wrote this again, because reasons",
    )


def test_bob_creates_alice_deletes_alice_restores(magic_folder):
    alice_dir, bob_dir = magic_folder

    # bob creates a file
    with open(join(bob_dir, "boom2"), "w") as f:
        f.write("bob wrote this")

    util.await_file_contents(
        join(alice_dir, "boom2"),
        "bob wrote this"
    )

    # alice deletes it (so bob should as well
    unlink(join(alice_dir, "boom2"))
    util.await_file_vanishes(join(bob_dir, "boom2"))

    # alice restore it, with new contents
    with open(join(alice_dir, "boom2"), "w") as f:
        f.write("alice re-wrote this again, because reasons")


# this sometimes fails on Travis
@pytest.mark.xfail
def test_bob_conflicts_with_alice_fresh(magic_folder):
    # both alice and bob make a file at "the same time".
    alice_dir, bob_dir = magic_folder

    # really, we fudge this a little: in reality, either alice or bob
    # "wins" by uploading to the DMD first. So we make sure bob wins
    # this one by giving him a massive head start
    with open(join(bob_dir, 'alpha'), 'w') as f:
        f.write("this is bob's alpha\n")
    time.sleep(1.0)
    with open(join(alice_dir, 'alpha'), 'w') as f:
        f.write("this is alice's alpha\n")

    # since bob uploaded first, alice should see a backup
    util.await_file_contents(join(alice_dir, 'alpha'), "this is bob's alpha\n")
    util.await_file_contents(join(alice_dir, 'alpha.backup'), "this is alice's alpha\n")

    util.await_file_contents(join(bob_dir, 'alpha'), "this is alice's alpha\n")
    util.await_file_contents(join(bob_dir, 'alpha.backup'), "this is bob's alpha\n")


# this sometimes fails on Travis
@pytest.mark.xfail
def test_bob_conflicts_with_alice_preexisting(magic_folder):
    # both alice and bob edit a file at "the same time" (similar to
    # above, but the file already exists before the edits)
    alice_dir, bob_dir = magic_folder

    # have bob create the file
    with open(join(bob_dir, 'beta'), 'w') as f:
        f.write("original beta (from bob)\n")
    util.await_file_contents(join(alice_dir, 'beta'), "original beta (from bob)\n")

    # both alice and bob now have a "beta" file, at version 0

    # really, we fudge this a little: in reality, either alice or bob
    # "wins" by uploading to the DMD first. So we make sure bob wins
    # this one by giving him a massive head start
    with open(join(bob_dir, 'beta'), 'w') as f:
        f.write("this is bob's beta\n")
    time.sleep(1.0)
    with open(join(alice_dir, 'beta'), 'w') as f:
        f.write("this is alice's beta\n")

    # since bob uploaded first, alice should see a backup
    util.await_file_contents(join(bob_dir, 'beta'), "this is bob's beta\n")
    util.await_file_contents(join(alice_dir, 'beta'), "this is bob's beta\n")
    util.await_file_contents(join(alice_dir, 'beta.backup'), "this is alice's beta\n")


@pytest.inlineCallbacks
def test_edmond_uploads_then_restarts(reactor, request, temp_dir, introducer_furl, flog_gatherer, storage_nodes):
    """
    ticket 2880: if a magic-folder client uploads something, then
    re-starts a spurious .backup file should not appear
    """

    edmond_dir = join(temp_dir, 'edmond')
    edmond = yield util._create_node(
        reactor, request, temp_dir, introducer_furl, flog_gatherer,
        "edmond", web_port="tcp:9985:interface=localhost",
        storage=False,
    )


    magic_folder = join(temp_dir, 'magic-edmond')
    mkdir(magic_folder)
    created = False
    # create a magic-folder
    # (how can we know that the grid is ready?)
    for _ in range(10):  # try 10 times
        try:
            proto = util._CollectOutputProtocol()
            transport = reactor.spawnProcess(
                proto,
                sys.executable,
                [
                    sys.executable, '-m', 'allmydata.scripts.runner',
                    'magic-folder', 'create',
                    '--poll-interval', '2',
                    '--basedir', edmond_dir,
                    'magik:',
                    'edmond_magic',
                    magic_folder,
                ]
            )
            yield proto.done
            created = True
            break
        except Exception as e:
            print("failed to create magic-folder: {}".format(e))
            time.sleep(1)

    assert created, "Didn't create a magic-folder"

    # to actually-start the magic-folder we have to re-start
    edmond.signalProcess('TERM')
    yield edmond._protocol.exited
    time.sleep(1)
    edmond = yield util._run_node(reactor, edmond._node_dir, request, 'Completed initial Magic Folder scan successfully')

    # add a thing to the magic-folder
    with open(join(magic_folder, "its_a_file"), "w") as f:
        f.write("edmond wrote this")

    # fixme, do status-update attempts in a loop below
    time.sleep(5)

    # let it upload; poll the HTTP magic-folder status API until it is
    # uploaded
    from allmydata.scripts.magic_folder_cli import _get_json_for_fragment

    with open(join(edmond_dir, u'private', u'api_auth_token'), 'rb') as f:
        token = f.read()

    uploaded = False
    for _ in range(10):
        options = {
            "node-url": open(join(edmond_dir, u'node.url'), 'r').read().strip(),
        }
        try:
            magic_data = _get_json_for_fragment(
                options,
                'magic_folder?t=json',
                method='POST',
                post_args=dict(
                    t='json',
                    name='default',
                    token=token,
                )
            )
            for mf in magic_data:
                if mf['status'] == u'success' and mf['path'] == u'its_a_file':
                    uploaded = True
                    break
        except Exception as e:
            time.sleep(1)

    assert uploaded, "expected to upload 'its_a_file'"

    # re-starting edmond right now would "normally" trigger the 2880 bug

    # kill edmond
    edmond.signalProcess('TERM')
    yield edmond._protocol.exited
    time.sleep(1)
    edmond = yield util._run_node(reactor, edmond._node_dir, request, 'Completed initial Magic Folder scan successfully')

    # XXX how can we say for sure if we've waited long enough? look at
    # tail of logs for magic-folder ... somethingsomething?
    print("waiting 20 seconds to see if a .backup appears")
    for _ in range(20):
        assert exists(join(magic_folder, "its_a_file"))
        assert not exists(join(magic_folder, "its_a_file.backup"))
        time.sleep(1)
