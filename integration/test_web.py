import sys
import time
import shutil
import json
from os import mkdir, unlink, utime
from os.path import join, exists, getmtime

import util

import pytest_twisted


def test_index(alice):
    """
    we can download the index file
    """
    util.web_get(alice._node_dir, "")
    # ...and json mode is valid json
    json.loads(util.web_get(alice._node_dir, "?t=json"))


def test_upload_download(alice):
    """
    upload a file, then download it via readcap
    """

    # XXX FIXME why?
    print("waiting for ready..")
    time.sleep(10)

    FILE_CONTENTS = "some contents"

    readcap = util.web_post(
        alice._node_dir,
        "uri",
        data={
            "t": "upload",
            "format": "mdmf",
        },
        files={
            "file": FILE_CONTENTS,
        },
    )
    readcap = readcap.strip()
    print("readcap '{}'".format(readcap))

    data = util.web_get(
        alice._node_dir, "uri",
        params={
            "uri": readcap,
            "filename": "boom",
        }
    )
    assert data == FILE_CONTENTS


def test_put(alice):
    """
    use PUT to create a file
    """

    import time; time.sleep(10) # XXX wat
    FILE_CONTENTS = "added via PUT"

    import requests
    resp = requests.put(
        util.node_url(alice._node_dir, "uri"),
        files={
            "file": FILE_CONTENTS,
        },
    )
    assert resp.text.strip().startswith("URI:CHK:")
    assert resp.text.strip().endswith(":2:4:153")


def test_helper_status(storage_nodes):
    """
    successfully GET the /helper_status page
    """

    import requests
    url = util.node_url(storage_nodes[0]._node_dir, "helper_status")
    print("GET {}".format(url))
    resp = requests.get(url)
    print(resp.text.strip())
