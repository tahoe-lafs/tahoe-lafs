import sys
import time
import shutil
import json
import urllib2
from os import mkdir, unlink, utime
from os.path import join, exists, getmtime

import allmydata.uri

import util

import requests
import pytest_twisted


def test_index(alice):
    """
    we can download the index file
    """
    util.web_get(alice.node_dir, u"")
    # ...and json mode is valid json
    json.loads(util.web_get(alice.node_dir, u"?t=json"))


def test_upload_download(alice):
    """
    upload a file, then download it via readcap
    """

    FILE_CONTENTS = "some contents"

    readcap = util.web_post(
        alice.node_dir,
        u"uri",
        data={
            u"t": u"upload",
            u"format": u"mdmf",
        },
        files={
            u"file": FILE_CONTENTS,
        },
    )
    readcap = readcap.strip()

    data = util.web_get(
        alice.node_dir, u"uri",
        params={
            u"uri": readcap,
            u"filename": u"boom",
        }
    )
    assert data == FILE_CONTENTS


def test_put(alice):
    """
    use PUT to create a file
    """

    FILE_CONTENTS = b"added via PUT"

    resp = requests.put(
        util.node_url(alice.node_dir, u"uri"),
        files={
            u"file": FILE_CONTENTS,
        },
    )
    cap = allmydata.uri.from_string(resp.text.strip().encode('ascii'))
    assert isinstance(cap, allmydata.uri.CHKFileURI)
    assert cap.size == 153
    assert cap.total_shares == 4
    assert cap.needed_shares == 2


def test_helper_status(storage_nodes):
    """
    successfully GET the /helper_status page
    """

    url = util.node_url(storage_nodes[0].node_dir, "helper_status")
    resp = requests.get(url)
    assert resp.status_code >= 200 and resp.status_code < 300


def test_deep_stats(alice):
    """
    create a directory, do deep-stats on it and prove the /operations/
    URIs work
    """
    resp = requests.post(
        util.node_url(alice.node_dir, "uri"),
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
    dircap_uri = util.node_url(alice.node_dir, "uri/{}".format(urllib2.quote(dircap)))

    # POST a file into this directory
    FILE_CONTENTS = b"a file in a directory"

    resp = requests.post(
        dircap_uri,
        data={
            u"t": u"upload",
            u"when_done": u".",
        },
        files={
            u"file": FILE_CONTENTS,
        },
    )

    # confirm the file is in the directory
    resp = requests.get(
        dircap_uri,
        params={
            u"t": u"json",
        },
    )
    d = json.loads(resp.content)
    k, data = d
    assert k == u"dirnode"
    assert len(data['children']) == 1
    k, child = data['children'].values()[0]
    assert k == u"filenode"
    assert child['size'] == len(FILE_CONTENTS)

    # perform deep-stats on it...
    resp = requests.post(
        dircap_uri,
        data={
            u"t": u"start-deep-stats",
            u"ophandle": u"something_random",
        },
    )
    assert resp.status_code >= 200 and resp.status_code < 300

    # confirm we get information from the op .. after its done
    while True:
        resp = requests.get(
            util.node_url(alice.node_dir, u"operations/something_random"),
        )
        d = json.loads(resp.content)
        if d['size-literal-files'] == len(FILE_CONTENTS):
            print("stats completed successfully")
            break
        else:
            print("{} != {}; waiting".format(d['size-literal-files'], len(FILE_CONTENTS)))
        time.sleep(.5)


def test_status(alice):
    """
    confirm we get something sensible from /status and the various sub-types
    """

    # upload a file
    # (because of the nature of the integration-tests, we can only
    # assert things about "our" file because we don't know what other
    # operations may have happened in the grid before our test runs).

    FILE_CONTENTS = b"all the Important Data of alice\n" * 1200

    resp = requests.put(
        util.node_url(alice.node_dir, u"uri"),
        data=FILE_CONTENTS,
    )
    cap = resp.text.strip()

    print("Uploaded data, cap={}".format(cap))
    resp = requests.get(
        util.node_url(alice.node_dir, u"uri/{}".format(urllib2.quote(cap))),
    )

    print("Downloaded {} bytes of data".format(len(resp.content)))
    assert resp.content == FILE_CONTENTS

    # find our upload and download status pages
    from twisted.web import microdom
    from collections import defaultdict
    from twisted.web import microdom
    from collections import defaultdict

    resp = requests.get(
        util.node_url(alice.node_dir, "status"),
    )
    dom = microdom.parseString(resp.content)

    # so, we *could* descend the DOM "more properly" .. or just look
    # at the URIs
    hrefs = set()
    for td in dom.getElementsByTagName('td'):
        for link in dom.getElementsByTagName('a'):
            hrefs.add(link.getAttribute('href'))

    found_upload = False
    found_download = False
    for href in hrefs:
        print("href: {}".format(href))
        if href.startswith(u"/") or not href:
            continue
        resp = requests.get(
            util.node_url(alice.node_dir, u"status/{}".format(href)),
        )
        if href.startswith(u'up'):
            assert "File Upload Status" in resp.content
            if "Total Size: {}".format(len(FILE_CONTENTS)) in resp.content:
                found_upload = True
        elif href.startswith(u'down'):
            print(href)
            assert "File Download Status" in resp.content
            if "Total Size: {}".format(len(FILE_CONTENTS)) in resp.content:
                found_download = True

    assert found_upload, "Failed to find the file we uploaded in the status-page"
    assert found_download, "Failed to find the file we downloaded in the status-page"
