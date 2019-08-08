# encoding: utf-8

from twisted.trial import unittest
from twisted.internet.task import Clock
from twisted.internet.defer import inlineCallbacks
from twisted.web.template import XMLString, Element

from nevow.testutil import FakeRequest, renderPage
from nevow.context import WebContext

from ...storage_client import NativeStorageServer
from ...web.root import Root
from ...util.connection_status import ConnectionStatus
from allmydata.client import SecretHolder

class FakeRoot(Root):
    def __init__(self):
        pass
    def now_fn(self):
        return 0

class FakeContext(object):
    def __init__(self):
        self.slots = {}
        self.tag = self
    def fillSlots(self, slotname, contents):
        self.slots[slotname] = contents

class RenderServiceRow(unittest.TestCase):

    def test_missing(self):
        # minimally-defined static servers just need anonymous-storage-FURL
        # and permutation-seed-base32. The WUI used to have problems
        # rendering servers that lacked nickname and version. This tests that
        # we can render such minimal servers.
        ann = {"anonymous-storage-FURL": "pb://w2hqnbaa25yw4qgcvghl5psa3srpfgw3@tcp:127.0.0.1:51309/vucto2z4fxment3vfxbqecblbf6zyp6x",
               "permutation-seed-base32": "w2hqnbaa25yw4qgcvghl5psa3srpfgw3",
               }
        s = NativeStorageServer("server_id", ann, None, {})
        cs = ConnectionStatus(False, "summary", {}, 0, 0)
        s.get_connection_status = lambda: cs

        r = FakeRoot()
        ctx = FakeContext()
        res = r.render_service_row(ctx, s)
        self.assertIdentical(res, ctx)
        self.assertEqual(ctx.slots["version"], "")
        self.assertEqual(ctx.slots["nickname"], "")


class FakeUploader(object):
    """
    """
    def get_helper_info(self):
        return ("furl", False)


class FakeHelper(object):
    def get_stats(self):
        return {
            "chk_upload_helper.active_uploads": 0,
        }


class FakeStorageBroker(object):
    def get_connected_servers(self):
        return {}

    def get_all_serverids(self):
        return ()

    def get_known_servers(self):
        return ()


class FakeMagicFolder(object):
    def get_public_status(self):
        return (True, ["this magic folder is alive"])


# XXX there are several 'fake client' instance throughout the code
# .. probably should be a single one that works better and covers all
# the cases etc.
class FakeClient(object):
    """
    just enough to let the node acquire an uploader (which it won't
    use) and at least one magic-folder for RenderRoot tests.
    """
    nickname = "fake_nickname"
    stats_provider = None
    uploader = FakeUploader()
    helper = FakeHelper()
    storage_broker = FakeStorageBroker()

    _secret_holder = SecretHolder("lease secret", "convergence secret")
    _magic_folders = {
        "foo": FakeMagicFolder(),
    }

    def get_long_nodeid(self):
        return "v0-nodeid"

    def get_long_tubid(self):
        return "v0-tubid"

    def introducer_connection_statuses(self):
        return {}

    def get_auth_token(self):
        return "x"

    def getServiceNamed(self, name):
        return {
            "uploader": self.uploader,
            "helper": self.helper,
        }[name]

    def get_encoding_parameters(self):
        return {"k": 3, "n": 10}

    def get_storage_broker(self):
        return self.storage_broker

    def get_history(self):
        return None



class RenderRoot(unittest.TestCase):
    """
    Test rendering of the root template.

    These tests are fairly fragile because they have 'actual HTML'
    burned into them -- they are here to prove that porting away from
    Nevow hasn't changed the rendering drastically (perhaps they
    should just be deleted or simlified after that).
    """

    def setUp(self):
        self.root = FakeRoot()
        self.context = WebContext()
        self.clock = Clock()
        self.client = FakeClient()

    def test_basic_stan(self):
        """
        we can render the root without any exceptions
        """

        class MyRoot(Element):
            loader = XMLString(GOLDEN_ROOT)

        request = FakeRequest()
        r = MyRoot()
        r.render(request)

    @inlineCallbacks
    def test_root_template(self):
        """
        The current root renders the same as it did with Nevow
        """
        page = Root(self.client, self.clock, now_fn=self.clock.seconds)
        page.addSlash = False  # XXX hack around what looks like nevow testutils bug

        page_data = yield renderPage(page)

        # we chop up to the line with "<footer>" because there's a
        # timestamp and a bunch of versions burned into that :/
        page_data = page_data[:page_data.find("<footer>")].rstrip()
        golden = GOLDEN_ROOT[:GOLDEN_ROOT.find("<footer>")].rstrip()

        self.assertEqual(page_data, golden)


GOLDEN_ROOT = """<!DOCTYPE html
  PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN"
  "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Tahoe-LAFS - Welcome</title>
    <meta content="width=device-width, initial-scale=1.0" name="viewport" />
    <meta content="Tahoe-LAFS is a free and open distributed storage system" name="description" />
    <meta content="Tahoe-LAFS" name="author" />

    <!-- Le styles -->
    <link href="/css/bootstrap.css" rel="stylesheet" />
    <link href="/css/new-tahoe.css" rel="stylesheet" />

    <!-- Le fav and touch icons -->
    <link href="/icon.png" rel="shortcut icon" />
  </head>

  <body>

    <div class="navbar navbar-fixed-top">
      <div class="navbar-inner">
        <div class="container-fluid">
          <a class="brand" href="/"><img alt="Tahoe-LAFS" src="/img/logo.png" /></a>
          <table class="node-info pull-right">
            <tr>
              <th>Nickname:</th>
              <td>fake_nickname</td>
            </tr>
            <tr>
              <th>Node ID:</th>
              <td title="TubID: v0-tubid">v0-nodeid</td>
            </tr>
          </table>
        </div>
      </div>
    </div>

    <div class="container-fluid">
      <div class="row-fluid">
        <div class="span3">
          <div class="well sidebar-nav nav">
             <div class="nav-header">Open Tahoe-URI:</div>
             <div class="nav-form">
               <form action="uri" enctype="multipart/form-data" method="get">
                 <input name="uri" type="text" />
                 <p><input class="btn" type="submit" value="View File or Directory »" /></p>
               </form>
            </div>
            <hr />

            <div class="nav-header">Download Tahoe-URI:</div>
            <div class="nav-form">
              <form action="uri" enctype="multipart/form-data" method="get">
                <label for="download-uri">
                  URI
                  <input name="uri" type="text" />
                </label>
                <label for="download-filename">
                  Filename
                  <input name="filename" type="text" />
                </label>
                <input name="save" type="hidden" value="true" />
                <p><input class="btn" type="submit" value="Download File »" /></p>
              </form>
            </div>
            <hr />

            <div class="nav-header">Upload File</div>
            <div class="nav-form">
              <form action="uri" enctype="multipart/form-data" method="post">
                <input class="freeform-input-file" name="file" type="file" />
                <input name="t" type="hidden" value="upload" />

                <label class="radio" for="upload-chk">
                  <input checked="checked" id="upload-chk" name="format" type="radio" value="chk" />
                  Immutable
                </label>

                <label class="radio" for="upload-sdmf">
                  <input id="upload-sdmf" name="format" type="radio" value="sdmf" />
                  <acronym title="Small Distributed Mutable File">SDMF</acronym>
                </label>

                <label class="radio" for="upload-mdmf">
                  <input id="upload-mdmf" name="format" type="radio" value="mdmf" />
                  <acronym title="Medium Distributed Mutable File">MDMF</acronym> (experimental)
                </label>

                <p><input class="btn" type="submit" value="Upload File »" /></p>
              </form>
            </div>
            <hr />

            <div class="nav-header">Create Directory</div>
            <div class="nav-form">
              <form action="uri" enctype="multipart/form-data" method="post">
                <label class="radio" for="mkdir-sdmf">
                  <input checked="checked" id="mkdir-sdmf" name="format" type="radio" value="sdmf" />
                  <acronym title="Small Distributed Mutable File">SDMF</acronym>
                </label>

                <label class="radio" for="mkdir-mdmf">
                  <input id="mkdir-mdmf" name="format" type="radio" value="mdmf" />
                  <acronym title="Medium Distributed Mutable File">MDMF</acronym> (experimental)
                </label>

                <input name="t" type="hidden" value="mkdir" />
                <input name="redirect_to_result" type="hidden" value="true" />
                <input class="btn" type="submit" value="Create a directory »" />
              </form>
            </div>

          </div><!--/.well -->
          <div class="well sidebar-nav">
            <div class="nav-header">
              <ul class="nav nav-list">
                <li class="nav-header">Tools</li>
                <li><a href="status">Recent and Active Operations</a></li>
                <li><a href="statistics">Operational Statistics</a></li>
              </ul>
            </div>
            <hr />
            <div class="nav-header">
              <ul class="nav nav-list">
                <li class="nav-header">Save incident report</li>
                <li><div><form action="report_incident" enctype="multipart/form-data" method="post"><fieldset><input name="t" type="hidden" value="report-incident" />What went wrong?  <input name="details" type="text" />  <input type="submit" value="Save »" /></fieldset></form></div></li>
              </ul>
            </div>
          </div><!--/.well -->
        </div><!--/span-->
        <div class="span9">
          <div style="margin-bottom: 16px">
            <h1 style="font-size: 48px">Grid Status</h1>
          </div>
          <div class="grid-status">
            <div class="row-fluid">
              <div class="span6">
                <div>
                  <h3>
                    <div class="status-indicator"><img alt="Disconnected" src="img/connected-no.png" /></div>
                    <div>No introducers connected</div>
                  </h3>
                </div>
                <div>
                  <h3>
                    <div class="status-indicator"><img alt="Disconnected" src="img/connected-no.png" /></div>
                    <div>Helper not connected</div>
                  </h3>
                  <div class="furl">/[censored]</div>
                </div>
              </div><!--/span-->
              <div class="span6">
                <div class="span4 services">
                  <h3>Services</h3>
                  <div><ul><li>Not running storage server</li><li>Helper: 0 active uploads</li></ul></div>
                </div><!--/span-->
              </div><!--/span-->
            </div><!--/row-->
          </div>

          <div class="row-fluid">
            <h2>Magic Folders</h2>
            <div><div><div class="status-indicator"><img alt="working" src="img/connected-yes.png" /></div><h3>foo</h3><ul class="magic-folder-status"><li>this magic folder is alive</li></ul></div></div>
          </div><!--/row-->

          <div class="row-fluid">
            <h2>
              Connected to <span>0</span>
              of <span>0</span> known storage servers
            </h2>
          </div><!--/row-->
          <table class="table table-striped table-bordered peer-status"><tr>
                <td><h3>Nickname</h3></td>
                <td><h3>Connection</h3></td>
                <td><h3>Last&nbsp;RX</h3></td>
                <td><h3>Version</h3></td>
                <td><h3>Available</h3></td>
              </tr><tr><td colspan="5">You are not presently connected to any servers.</td></tr></table>
          <div class="row-fluid">
            <h2>Connected to <span>0</span> of <span>0</span> introducers</h2>
          </div>
          <table class="table table-striped table-bordered peer-status"><tr>
                <td><h3>Connection</h3></td>
                <td><h3>Last&nbsp;RX</h3></td>
              </tr><tr><td colspan="2">No introducers are configured.</td></tr></table>
        </div><!--/span-->
      </div><!--/row-->

      <hr />

      <footer>
        <p>© <a href="https://tahoe-lafs.org/">Tahoe-LAFS Software Foundation 2013-2016</a></p>
        <p class="minutia">Page rendered at <span>2019-07-30 15:32:19</span></p>
        <p class="minutia">tahoe-lafs: 1.13.0.post1003.dev0 [ticket3227-remove-nevow: aa6eba1ce8220e5f794864063d7900686e0fb3ad-dirty]
foolscap: 0.13.1
zfec: 1.5.3
Twisted: 19.2.1
Nevow: 0.14.4
zope.interface: unknown
python: 2.7.13
platform: Linux-debian_9.9-x86_64-64bit
pyOpenSSL: 19.0.0
OpenSSL: 1.1.1c [ 28 May 2019]
pyasn1: 0.4.5
service-identity: 18.1.0
characteristic: 14.3.0
pyasn1-modules: 0.2.5
cryptography: 2.7
cffi: 1.12.3
six: 1.12.0
enum34: 1.1.6
pycparser: 2.19
PyYAML: 5.1.1
magic-wormhole: 0.11.2
setuptools: 41.0.1
eliot: 1.7.0
attrs: 19.1.0
autobahn: 19.7.2 [according to pkg_resources]
txtorcon: 19.0.0 [according to pkg_resources]
constantly: 15.1.0 [according to pkg_resources]
tqdm: 4.32.2 [according to pkg_resources]
automat: 0.7.0 [according to pkg_resources]
boltons: 19.1.0 [according to pkg_resources]
click: 7.0 [according to pkg_resources]
appdirs: 1.4.3 [according to pkg_resources]
ipaddress: 1.0.22 [according to pkg_resources]
humanize: 0.5.1 [according to pkg_resources]
hkdf: 0.0.3 [according to pkg_resources]
bcrypt: 3.1.7 [according to pkg_resources]
txaio: 18.8.1 [according to pkg_resources]
pynacl: 1.3.0 [according to pkg_resources]
idna: 2.8 [according to pkg_resources]
hyperlink: 19.0.0 [according to pkg_resources]
spake2: 0.8 [according to pkg_resources]
pyhamcrest: 1.9.0 [according to pkg_resources]
pyrsistent: 0.15.4 [according to pkg_resources]
incremental: 17.5.0 [according to pkg_resources]
asn1crypto: 0.24.0 [according to pkg_resources]
</p>
        <p class="minutia">Tahoe-LAFS code imported from: <span>&lt;module 'allmydata' from '/home/mike/work-lafs/src/tahoe-lafs/src/allmydata/__init__.pyc'&gt;</span></p>
      </footer>

    </div><!--/.fluid-container-->
  </body>
</html>"""
