import sys
import time
import shutil
from os import mkdir, unlink, utime
from os.path import join, exists, getmtime

import util

import pytest_twisted


def test_index(alice):
    """
    we can download the index file
    """
    util.web_get(alice._node_dir, "")


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
