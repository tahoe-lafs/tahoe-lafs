# So these dummy tests run first and instantiate the pre-requisites
# first (e.g. introducer) and therefore print "something" on the
# console as we go (a . or the test-name in "-v"/verbose mode)

# You can safely skip any of these tests, it'll just appear to "take
# longer" to start the first test as the fixtures get built


def test_create_flogger(flog_gatherer):
    print("Created flog_gatherer")


def test_create_introducer(introducer):
    print("Created introducer")


def test_create_storage(storage_nodes):
    print("Created {} storage nodes".format(len(storage_nodes)))


def test_create_alice_bob_magicfolder(magic_folder):
    print("Alice and Bob have paired magic-folders")
