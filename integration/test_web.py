import sys
import time
import shutil
import json
import urllib2
from os import mkdir, unlink, utime
from os.path import join, exists, getmtime

import util

import requests
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

    FILE_CONTENTS = "added via PUT"

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

    url = util.node_url(storage_nodes[0]._node_dir, "helper_status")
    resp = requests.get(url)
    assert resp.status_code >= 200 and resp.status_code < 300


def test_deep_stats(alice):
    """
    create a directory, do deep-stats on it and prove the /operations/
    URIs work
    """
    resp = requests.post(
        util.node_url(alice._node_dir, "uri"),
        params={
            "format": "sdmf",
            "t": "mkdir",
            "redirect_to_result": "true",
        },
    )
    assert resp.status_code >= 200 and resp.status_code < 300

    # when creating a directory, we'll be re-directed to a URL
    # containing our writecap.. (XXX doesn't this violate the "URLs
    # leak" maxim?)
    uri = urllib2.unquote(resp.url)
    assert 'URI:DIR2:' in uri
    dircap = uri[uri.find("URI:DIR2:"):].rstrip('/')
    dircap_uri = util.node_url(alice._node_dir, "uri/{}".format(urllib2.quote(dircap)))

    # POST a file into this directory
    FILE_CONTENTS = "a file in a directory"

    resp = requests.post(
        dircap_uri,
        data={
            "t": "upload",
            "when_done": ".",
        },
        files={
            "file": FILE_CONTENTS,
        },
    )

    # confirm the file is in the directory
    resp = requests.get(
        dircap_uri,
        params={
            "t": "json",
        },
    )
    d = json.loads(resp.content)
    k, data = d
    assert k == "dirnode"
    assert len(data['children']) == 1
    k, child = data['children'].values()[0]
    assert k == "filenode"
    assert child['size'] == len(FILE_CONTENTS)

    # perform deep-stats on it...
    resp = requests.post(
        dircap_uri,
        data={
            "t": "start-deep-stats",
            "ophandle": "something_random",
        },
    )
    assert resp.status_code >= 200 and resp.status_code < 300

    # confirm we get information from the op .. after its done
    while True:
        resp = requests.get(
            util.node_url(alice._node_dir, "operations/something_random"),
        )
        d = json.loads(resp.content)
        if d['size-literal-files'] == len(FILE_CONTENTS):
            print("stats completed successfully")
            break
        else:
            print("{} != {}; waiting".format(d['size-literal-files'], len(FILE_CONTENTS)))
        time.sleep(.5)
