
import os, pickle
import sqlite3 as sqlite

import urllib
import simplejson
from allmydata.scripts.common_http import do_http
from allmydata.scripts.tahoe_backup import parse_old_timestamp, readonly, \
     raiseHTTPError, HTTPError
from allmydata.util import hashutil, base32
from allmydata.util.netstring import netstring
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS

TAG = "consolidator_dirhash_v1"

class CycleDetected(Exception):
    pass


class Consolidator:
    def __init__(self, options):
        self.options = options
        self.rootcap, path = get_alias(options.aliases, options.where,
                                       DEFAULT_ALIAS)
        assert path == ""
        self.dbfile = options["dbfile"]
        assert self.dbfile, "--dbfile is required"
        self.backupfile = options["backupfile"]
        assert self.backupfile, "--backupfile is required"
        self.nodeurl = options["node-url"]
        if not self.nodeurl.endswith("/"):
            self.nodeurl += "/"
        self.must_rescan_readonly_snapshots = not os.path.exists(self.dbfile)
        self.db = sqlite.connect(self.dbfile)
        self.cursor = self.db.cursor()
        try:
            self.cursor.execute("CREATE TABLE dirhashes"
                                "("
                                " dirhash TEXT PRIMARY KEY,"
                                " dircap TEXT"
                                ")")
        except sqlite.OperationalError, e:
            if "table dirhashes already exists" not in str(e):
                raise

    def read_directory_json(self, dircap):
        url = self.nodeurl + "uri/%s?t=json" % urllib.quote(dircap)
        resp = do_http("GET", url)
        if resp.status != 200:
            raiseHTTPError("Error during directory GET", resp)
        jd = simplejson.load(resp)
        ntype, ndata = jd
        if ntype != "dirnode":
            return None
        return ndata

    def msg(self, text):
        print >>self.options.stdout, text
    def err(self, text):
        print >>self.options.stderr, text

    def consolidate(self):
        try:
            data = self.read_directory_json(self.rootcap + "/Backups")
        except HTTPError:
            self.err("Unable to list /Backups, maybe this account has none?")
            return 1
        kids = data["children"]
        potential_systems = {}
        for (childname, (childtype, childdata)) in kids.items():
            if childtype != "dirnode":
                continue
            potential_systems[childname] = str(childdata["rw_uri"])
        backup_data = {"Backups": data, "systems": {}, "archives": {}}
        systems = {}
        for name, sdircap in potential_systems.items():
            sdata = self.read_directory_json(sdircap)
            kids = sdata["children"]
            if not u"Archives" in kids and not u"Latest Backup" in kids:
                self.msg("%s: not a backupdir, no 'Archives' and 'Latest'" % name)
                continue
            self.msg("%s is a system" % name)
            backup_data["systems"][name] = sdata
            archives_dircap = kids[u"Archives"][1]["rw_uri"]
            archives_data = self.read_directory_json(archives_dircap)
            backup_data["archives"][name] = archives_data
            systems[name] = archives_dircap
        if not systems:
            self.msg("No systems under /Backups, nothing to consolidate")
            return 0
        backupfile = self.backupfile
        counter = 0
        while os.path.exists(backupfile):
            backupfile = self.backupfile + "." + str(counter)
            counter += 1
        f = open(backupfile, "wb")
        pickle.dump(backup_data, f)
        f.close()

        for name, archives_dircap in sorted(systems.items()):
            self.do_system(name, archives_dircap)
        return 0

    def do_system(self, system_name, archives_dircap):
        # first we walk through the Archives list, looking for the existing
        # snapshots. Each one will have a $NAME like "2008-11-16 10.34 PM"
        # (in various forms: we use tahoe_backup.parse_old_timestamp to
        # interpret it). At first, they'll all have $NAME and be writecaps.
        # As we run, we will create $NAME-readonly (with a readcap) for each
        # one (the first one will just be the readonly equivalent of the
        # oldest snapshot: the others will be constructed out of shared
        # directories). When we're done we'll have a $NAME-readonly for
        # everything except the latest snapshot (to avoid any danger of
        # modifying a backup that's already in progress). The very last step,
        # which won't be enabled until we're sure that everything is working
        # correctly, will replace each $NAME with $NAME-readonly.

        # We maintain a table that maps dirhash (hash of directory contents)
        # to a directory readcap which contains those contents. We use this
        # to decide if we can link to an existing directory, or if we must
        # create a brand new one. Usually we add to this table in two places:
        # when we scan the oldest snapshot (which we've just converted to
        # readonly form), and when we must create a brand new one. If the
        # table doesn't exist (probably because we've manually deleted it),
        # we will scan *all* the existing readonly snapshots, and repopulate
        # the table. We keep this table in a SQLite database (rather than a
        # pickle) because we want to update it persistently after every
        # directory creation, and writing out a 10k entry pickle takes about
        # 250ms

        # 'snapshots' maps timestamp to [rwname, writecap, roname, readcap].
        # The possibilities are:
        #  [$NAME, writecap, None, None] : haven't touched it
        #  [$NAME, writecap, $NAME-readonly, readcap] : processed, not replaced
        #  [None, None, $NAME, readcap] : processed and replaced

        self.msg("consolidating system %s" % system_name)
        self.directories_reused = 0
        self.directories_used_as_is = 0
        self.directories_created = 0
        self.directories_seen = set()
        self.directories_used = set()

        data = self.read_directory_json(archives_dircap)
        snapshots = {}

        for (childname, (childtype, childdata)) in data["children"].items():
            if childtype != "dirnode":
                self.msg("non-dirnode %s in Archives/" % childname)
                continue
            timename = childname
            if childname.endswith("-readonly"):
                timename = childname[:-len("-readonly")]
            timestamp = parse_old_timestamp(timename, self.options)
            assert timestamp is not None, timename
            snapshots.setdefault(timestamp, [None, None, None, None])
            # if the snapshot is readonly (i.e. it has no rw_uri), we might
            # need to re-scan it
            is_readonly = not childdata.has_key("rw_uri")
            if is_readonly:
                readcap = str(childdata["ro_uri"])
                if self.must_rescan_readonly_snapshots:
                    self.scan_old_directory(str(childdata["ro_uri"]))
                snapshots[timestamp][2] = childname
                snapshots[timestamp][3] = readcap
            else:
                writecap = str(childdata["rw_uri"])
                snapshots[timestamp][0] = childname
                snapshots[timestamp][1] = writecap
        else:
            self.msg(" No snapshots in Backups/Archives")
        snapshots = [ [timestamp] + values
                      for (timestamp, values) in snapshots.items() ]
        # now 'snapshots' is [timestamp, rwname, writecap, roname, readcap],
        # which makes it easier to process in temporal order
        snapshots.sort()
        self.msg(" %d snapshots" % len(snapshots))
        # we always ignore the last one, for safety
        snapshots = snapshots[:-1]

        first_snapshot = True
        for i,(timestamp, rwname, writecap, roname, readcap) in enumerate(snapshots):
            start_created = self.directories_created
            start_used_as_is = self.directories_used_as_is
            start_reused = self.directories_reused

            # [None, None, $NAME, readcap] : processed and replaced
            # [$NAME, writecap, $NAME-readonly, readcap] : processed, not replaced
            # [$NAME, writecap, None, None] : haven't touched it

            if readcap and not writecap:
                # skip past anything we've already processed and replaced
                assert roname
                assert not rwname
                first_snapshot = False
                self.msg(" %s already readonly" % roname)
                continue
            if readcap and writecap:
                # we've processed it, creating a -readonly version, but we
                # haven't replaced it.
                assert roname
                assert rwname
                first_snapshot = False
                self.msg(" %s processed but not yet replaced" % roname)
                if self.options["really"]:
                    self.msg("  replacing %s with %s" % (rwname, roname))
                    self.put_child(archives_dircap, rwname, readcap)
                    self.delete_child(archives_dircap, roname)
                continue
            assert writecap
            assert rwname
            assert not readcap
            assert not roname
            roname = rwname + "-readonly"
            # for the oldest snapshot, we can do a simple readonly conversion
            if first_snapshot:
                first_snapshot = False
                readcap = readonly(writecap)
                self.directories_used_as_is += 1
                self.msg(" %s: oldest snapshot, using as-is" % rwname)
                self.scan_old_directory(readcap)
            else:
                # for the others, we must scan their contents and build up a new
                # readonly directory (which shares common subdirs with previous
                # backups)
                self.msg(" %s: processing (%d/%d)" % (rwname, i+1, len(snapshots)))
                readcap = self.process_directory(readonly(writecap), (rwname,))
            if self.options["really"]:
                self.msg("  replaced %s" % rwname)
                self.put_child(archives_dircap, rwname, readcap)
            else:
                self.msg("  created %s" % roname)
                self.put_child(archives_dircap, roname, readcap)

            snapshot_created = self.directories_created - start_created
            snapshot_used_as_is = self.directories_used_as_is - start_used_as_is
            snapshot_reused = self.directories_reused - start_reused
            self.msg("  %s: done: %d dirs created, %d used as-is, %d reused"
                     % (rwname,
                        snapshot_created, snapshot_used_as_is, snapshot_reused))
        # done!
        self.msg(" system done, dircounts: %d/%d seen/used, %d created, %d as-is, %d reused" \
                 % (len(self.directories_seen), len(self.directories_used),
                    self.directories_created, self.directories_used_as_is,
                    self.directories_reused))

    def process_directory(self, readcap, path):
        # I walk all my children (recursing over any subdirectories), build
        # up a table of my contents, then see if I can re-use an old
        # directory with the same contents. If not, I create a new directory
        # for my contents. In all cases I return a directory readcap that
        # points to my contents.

        assert isinstance(readcap, str)
        self.directories_seen.add(readcap)

        # build up contents to pass to mkdir() (which uses t=set_children)
        contents = {} # childname -> (type, rocap, metadata)
        data = self.read_directory_json(readcap)
        assert data is not None
        hashkids = []
        children_modified = False
        for (childname, (childtype, childdata)) in sorted(data["children"].items()):
            if childtype == "dirnode":
                childpath = path + (childname,)
                old_childcap = str(childdata["ro_uri"])
                childcap = self.process_directory(old_childcap, childpath)
                if childcap != old_childcap:
                    children_modified = True
                contents[childname] = ("dirnode", childcap, None)
            else:
                childcap = str(childdata["ro_uri"])
                contents[childname] = (childtype, childcap, None)
            hashkids.append( (childname, childcap) )

        dirhash = self.hash_directory_contents(hashkids)
        old_dircap = self.get_old_dirhash(dirhash)
        if old_dircap:
            if self.options["verbose"]:
                self.msg("   %s: reused" % "/".join(path))
            assert isinstance(old_dircap, str)
            self.directories_reused += 1
            self.directories_used.add(old_dircap)
            return old_dircap
        if not children_modified:
            # we're allowed to use this directory as-is
            if self.options["verbose"]:
                self.msg("   %s: used as-is" % "/".join(path))
            new_dircap = readonly(readcap)
            assert isinstance(new_dircap, str)
            self.store_dirhash(dirhash, new_dircap)
            self.directories_used_as_is += 1
            self.directories_used.add(new_dircap)
            return new_dircap
        # otherwise, we need to create a new directory
        if self.options["verbose"]:
            self.msg("   %s: created" % "/".join(path))
        new_dircap = readonly(self.mkdir(contents))
        assert isinstance(new_dircap, str)
        self.store_dirhash(dirhash, new_dircap)
        self.directories_created += 1
        self.directories_used.add(new_dircap)
        return new_dircap

    def put_child(self, dircap, childname, childcap):
        url = self.nodeurl + "uri/%s/%s?t=uri" % (urllib.quote(dircap),
                                                  urllib.quote(childname))
        resp = do_http("PUT", url, childcap)
        if resp.status not in (200, 201):
            raiseHTTPError("error during put_child", resp)

    def delete_child(self, dircap, childname):
        url = self.nodeurl + "uri/%s/%s" % (urllib.quote(dircap),
                                            urllib.quote(childname))
        resp = do_http("DELETE", url)
        if resp.status not in (200, 201):
            raiseHTTPError("error during delete_child", resp)

    def mkdir(self, contents):
        url = self.nodeurl + "uri?t=mkdir"
        resp = do_http("POST", url)
        if resp.status < 200 or resp.status >= 300:
            raiseHTTPError("error during mkdir", resp)
        dircap = str(resp.read().strip())
        url = self.nodeurl + "uri/%s?t=set_children" % urllib.quote(dircap)
        body = dict([ (childname, (contents[childname][0],
                                   {"ro_uri": contents[childname][1],
                                    "metadata": contents[childname][2],
                                    }))
                      for childname in contents
                      ])
        resp = do_http("POST", url, simplejson.dumps(body))
        if resp.status != 200:
            raiseHTTPError("error during set_children", resp)
        return dircap

    def scan_old_directory(self, dircap, ancestors=()):
        # scan this directory (recursively) and stash a hash of its contents
        # in the DB. This assumes that all subdirs can be used as-is (i.e.
        # they've already been declared immutable)
        dircap = readonly(dircap)
        if dircap in ancestors:
            raise CycleDetected
        ancestors = ancestors + (dircap,)
        #self.visited.add(dircap)
        # TODO: we could use self.visited as a mapping from dircap to dirhash,
        # to avoid re-scanning old shared directories multiple times
        self.directories_seen.add(dircap)
        self.directories_used.add(dircap)
        data = self.read_directory_json(dircap)
        kids = []
        for (childname, (childtype, childdata)) in data["children"].items():
            childcap = str(childdata["ro_uri"])
            if childtype == "dirnode":
                self.scan_old_directory(childcap, ancestors)
            kids.append( (childname, childcap) )
        dirhash = self.hash_directory_contents(kids)
        self.store_dirhash(dirhash, dircap)
        return dirhash

    def hash_directory_contents(self, kids):
        kids.sort()
        s = "".join([netstring(childname.encode("utf-8"))+netstring(childcap)
                     for (childname, childcap) in kids])
        return hashutil.tagged_hash(TAG, s)

    def store_dirhash(self, dirhash, dircap):
        assert isinstance(dircap, str)
        # existing items should prevail
        try:
            c = self.cursor
            c.execute("INSERT INTO dirhashes (dirhash, dircap) VALUES (?,?)",
                      (base32.b2a(dirhash), dircap))
            self.db.commit()
        except sqlite.IntegrityError:
            # already present
            pass

    def get_old_dirhash(self, dirhash):
        self.cursor.execute("SELECT dircap FROM dirhashes WHERE dirhash=?",
                            (base32.b2a(dirhash),))
        row = self.cursor.fetchone()
        if not row:
            return None
        (dircap,) = row
        return str(dircap)


def main(options):
    c = Consolidator(options)
    return c.consolidate()
