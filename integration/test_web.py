"""
These tests were originally written to achieve some level of
coverage for the WebAPI functionality during Python3 porting (there
aren't many tests of the Web API period).

Most of the tests have cursory asserts and encode 'what the WebAPI did
at the time of testing' -- not necessarily a cohesive idea of what the
WebAPI *should* do in every situation. It's not clear the latter
exists anywhere, however.
"""

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
import html5lib
from bs4 import BeautifulSoup


def test_index(alice):
    """
    we can download the index file
    """
    util.web_get(alice, u"")


def test_index_json(alice):
    """
    we can download the index file as json
    """
    data = util.web_get(alice, u"", params={u"t": u"json"})
    # it should be valid json
    json.loads(data)


def test_upload_download(alice):
    """
    upload a file, then download it via readcap
    """

    FILE_CONTENTS = u"some contents"

    readcap = util.web_post(
        alice, u"uri",
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
        alice, u"uri",
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

    FILE_CONTENTS = b"added via PUT" * 20

    resp = requests.put(
        util.node_url(alice.node_dir, u"uri"),
        data=FILE_CONTENTS,
    )
    cap = allmydata.uri.from_string(resp.text.strip().encode('ascii'))
    cfg = alice.get_config()
    assert isinstance(cap, allmydata.uri.CHKFileURI)
    assert cap.size == len(FILE_CONTENTS)
    assert cap.total_shares == int(cfg.get_config("client", "shares.total"))
    assert cap.needed_shares == int(cfg.get_config("client", "shares.needed"))


def test_helper_status(storage_nodes):
    """
    successfully GET the /helper_status page
    """

    url = util.node_url(storage_nodes[0].node_dir, "helper_status")
    resp = requests.get(url)
    assert resp.status_code >= 200 and resp.status_code < 300
    dom = BeautifulSoup(resp.content, "html5lib")
    assert unicode(dom.h1.string) == u"Helper Status"


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
    # containing our writecap..
    uri = urllib2.unquote(resp.url)
    assert 'URI:DIR2:' in uri
    dircap = uri[uri.find("URI:DIR2:"):].rstrip('/')
    dircap_uri = util.node_url(alice.node_dir, "uri/{}".format(urllib2.quote(dircap)))

    # POST a file into this directory
    FILE_CONTENTS = u"a file in a directory"

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
    tries = 10
    while tries > 0:
        tries -= 1
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

    FILE_CONTENTS = u"all the Important Data of alice\n" * 1200

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

    resp = requests.get(
        util.node_url(alice.node_dir, "status"),
    )
    dom = html5lib.parse(resp.content)

    hrefs = [
        a.get('href')
        for a in dom.iter(u'{http://www.w3.org/1999/xhtml}a')
    ]

    found_upload = False
    found_download = False
    for href in hrefs:
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
            assert "File Download Status" in resp.content
            if "Total Size: {}".format(len(FILE_CONTENTS)) in resp.content:
                found_download = True

                # download the specialized event information
                resp = requests.get(
                    util.node_url(alice.node_dir, u"status/{}/event_json".format(href)),
                )
                js = json.loads(resp.content)
                # there's usually just one "read" operation, but this can handle many ..
                total_bytes = sum([st['bytes_returned'] for st in js['read']], 0)
                assert total_bytes == len(FILE_CONTENTS)


    assert found_upload, "Failed to find the file we uploaded in the status-page"
    assert found_download, "Failed to find the file we downloaded in the status-page"


def test_directory_deep_check(alice):
    """
    use deep-check and confirm the result pages work
    """

    # create a directory
    resp = requests.post(
        util.node_url(alice.node_dir, u"uri"),
        params={
            u"t": u"mkdir",
            u"redirect_to_result": u"true",
        }
    )

    # get json information about our directory
    dircap_url = resp.url
    resp = requests.get(
        dircap_url,
        params={u"t": u"json"},
    )
    dir_meta = json.loads(resp.content)

    # upload a file of pangrams into the directory
    FILE_CONTENTS = u"Sphinx of black quartz, judge my vow.\n" * (2048*10)

    resp = requests.post(
        dircap_url,
        params={
            u"t": u"upload",
            u"upload-chk": u"upload-chk",
        },
        files={
            u"file": FILE_CONTENTS,
        }
    )
    cap0 = resp.content
    print("Uploaded data0, cap={}".format(cap0))

    # a different pangram
    FILE_CONTENTS = u"The five boxing wizards jump quickly.\n" * (2048*10)

    resp = requests.post(
        dircap_url,
        params={
            u"t": u"upload",
            u"upload-chk": u"upload-chk",
        },
        files={
            u"file": FILE_CONTENTS,
        }
    )
    cap1 = resp.content
    print("Uploaded data1, cap={}".format(cap1))

    resp = requests.get(
        util.node_url(alice.node_dir, u"uri/{}".format(urllib2.quote(cap0))),
        params={u"t": u"info"},
    )

    def check_repair_data(checkdata):
        assert checkdata["healthy"] is True
        assert checkdata["count-happiness"] == 4
        assert checkdata["count-good-share-hosts"] == 4
        assert checkdata["count-shares-good"] == 4
        assert checkdata["count-corrupt-shares"] == 0
        assert checkdata["list-corrupt-shares"] == []

    # do a "check" (once for HTML, then with JSON for easier asserts)
    resp = requests.post(
        dircap_url,
        params={
            u"t": u"check",
            u"return_to": u".",
            u"verify": u"true",
        }
    )
    resp = requests.post(
        dircap_url,
        params={
            u"t": u"check",
            u"return_to": u".",
            u"verify": u"true",
            u"output": u"JSON",
        }
    )
    check_repair_data(json.loads(resp.content)["results"])

    # "check and repair"
    resp = requests.post(
        dircap_url,
        params={
            u"t": u"check",
            u"return_to": u".",
            u"verify": u"true",
            u"repair": u"true",
        }
    )
    resp = requests.post(
        dircap_url,
        params={
            u"t": u"check",
            u"return_to": u".",
            u"verify": u"true",
            u"repair": u"true",
            u"output": u"JSON",
        }
    )
    check_repair_data(json.loads(resp.content)["post-repair-results"]["results"])

    # start a "deep check and repair"
    resp = requests.post(
        dircap_url,
        params={
            u"t": u"start-deep-check",
            u"return_to": u".",
            u"verify": u"on",
            u"repair": u"on",
            u"output": u"JSON",
            u"ophandle": u"deadbeef",
        }
    )
    deepcheck_uri = resp.url

    data = json.loads(resp.content)
    tries = 10
    while not data['finished'] and tries > 0:
        tries -= 1
        time.sleep(0.5)
        print("deep-check not finished, reloading")
        resp = requests.get(deepcheck_uri, params={u"output": "JSON"})
        data = json.loads(resp.content)
    print("deep-check finished")
    assert data[u"stats"][u"count-immutable-files"] == 1
    assert data[u"stats"][u"count-literal-files"] == 0
    assert data[u"stats"][u"largest-immutable-file"] == 778240
    assert data[u"count-objects-checked"] == 2

    # also get the HTML version
    resp = requests.post(
        dircap_url,
        params={
            u"t": u"start-deep-check",
            u"return_to": u".",
            u"verify": u"on",
            u"repair": u"on",
            u"ophandle": u"definitely_random",
        }
    )
    deepcheck_uri = resp.url

    # if the operations isn't done, there's an <H2> tag with the
    # reload link; otherwise there's only an <H1> tag..wait up to 5
    # seconds for this to respond properly.
    for _ in range(5):
        resp = requests.get(deepcheck_uri)
        dom = BeautifulSoup(resp.content, "html5lib")
        if dom.h1 and u'Results' in unicode(dom.h1.string):
            break
        if dom.h2 and dom.h2.a and u"Reload" in unicode(dom.h2.a.string):
            dom = None
            time.sleep(1)
    assert dom is not None, "Operation never completed"


def test_storage_info(storage_nodes):
    """
    retrieve and confirm /storage URI for one storage node
    """
    storage0 = storage_nodes[0]

    requests.get(
        util.node_url(storage0.node_dir, u"storage"),
    )


def test_storage_info_json(storage_nodes):
    """
    retrieve and confirm /storage?t=json URI for one storage node
    """
    storage0 = storage_nodes[0]

    resp = requests.get(
        util.node_url(storage0.node_dir, u"storage"),
        params={u"t": u"json"},
    )
    data = json.loads(resp.content)
    assert data[u"stats"][u"storage_server.reserved_space"] == 1000000000


def test_introducer_info(introducer):
    """
    retrieve and confirm /introducer URI for the introducer
    """
    resp = requests.get(
        util.node_url(introducer.node_dir, u""),
    )
    assert "Introducer" in resp.content

    resp = requests.get(
        util.node_url(introducer.node_dir, u""),
        params={u"t": u"json"},
    )
    data = json.loads(resp.content)
    assert "announcement_summary" in data
    assert "subscription_summary" in data


def test_mkdir_with_children(alice):
    """
    create a directory using ?t=mkdir-with-children
    """

    # create a file to put in our directory
    FILE_CONTENTS = u"some file contents\n" * 500
    resp = requests.put(
        util.node_url(alice.node_dir, u"uri"),
        data=FILE_CONTENTS,
    )
    filecap = resp.content.strip()

    # create a (sub) directory to put in our directory
    resp = requests.post(
        util.node_url(alice.node_dir, u"uri"),
        params={
            u"t": u"mkdir",
        }
    )
    # (we need both the read-write and read-only URIs I guess)
    dircap = resp.content
    dircap_obj = allmydata.uri.from_string(dircap)
    dircap_ro = dircap_obj.get_readonly().to_string()

    # create json information about our directory
    meta = {
        "a_file": [
            "filenode", {
                "ro_uri": filecap,
                "metadata": {
                    "ctime": 1202777696.7564139,
                    "mtime": 1202777696.7564139,
                    "tahoe": {
                        "linkcrtime": 1202777696.7564139,
                        "linkmotime": 1202777696.7564139
                    }
                }
            }
        ],
        "some_subdir": [
            "dirnode", {
                "rw_uri": dircap,
                "ro_uri": dircap_ro,
                "metadata": {
                    "ctime": 1202778102.7589991,
                    "mtime": 1202778111.2160511,
                    "tahoe": {
                        "linkcrtime": 1202777696.7564139,
                        "linkmotime": 1202777696.7564139
                    }
                }
            }
        ]
    }

    # create a new directory with one file and one sub-dir (all-at-once)
    resp = util.web_post(
        alice, u"uri",
        params={u"t": "mkdir-with-children"},
        data=json.dumps(meta),
    )
    assert resp.startswith("URI:DIR2")
    cap = allmydata.uri.from_string(resp)
    assert isinstance(cap, allmydata.uri.DirectoryURI)
