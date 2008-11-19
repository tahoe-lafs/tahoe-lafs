
from allmydata.util import base32
from allmydata.util.abbreviate import abbreviate_space_both
from allmydata import uri
from allmydata.scripts.slow_operation import SlowOperationRunner

class ManifestGrabber(SlowOperationRunner):

    def make_url(self, base, ophandle):
        return base + "?t=start-manifest&ophandle=" + ophandle

    def write_results(self, data):
        stdout = self.options.stdout
        stderr = self.options.stderr
        if self.options["storage-index"]:
            for (path, cap) in data["manifest"]:
                u = uri.from_string(str(cap))
                si = u.get_storage_index()
                if si is not None:
                    print >>stdout, base32.b2a(si)
        else:
            for (path, cap) in data["manifest"]:
                try:
                    print >>stdout, cap, "/".join(path)
                except UnicodeEncodeError:
                    print >>stdout, cap, "/".join([p.encode("utf-8")
                                                   for p in path])

def manifest(options):
    return ManifestGrabber().run(options)

class StatsGrabber(SlowOperationRunner):

    def make_url(self, base, ophandle):
        return base + "?t=start-deep-stats&ophandle=" + ophandle

    def write_results(self, data):
        stdout = self.options.stdout
        stderr = self.options.stderr
        keys = ("count-immutable-files",
                "count-mutable-files",
                "count-literal-files",
                "count-files",
                "count-directories",
                "size-immutable-files",
                "size-mutable-files",
                "size-literal-files",
                "size-directories",
                "largest-directory",
                "largest-immutable-file",
                )
        width = max([len(k) for k in keys])
        print >>stdout, "Counts and Total Sizes:"
        for k in keys:
            fmt = "%" + str(width) + "s: %d"
            if k in data:
                value = data[k]
                if not k.startswith("count-") and value > 1000:
                    absize = abbreviate_space_both(value)
                    print >>stdout, fmt % (k, data[k]), "  ", absize
                else:
                    print >>stdout, fmt % (k, data[k])
        print >>stdout, "Size Histogram:"
        prevmax = None
        maxlen = max([len(str(maxsize))
                      for (minsize, maxsize, count)
                      in data["size-files-histogram"]])
        maxcountlen = max([len(str(count))
                           for (minsize, maxsize, count)
                           in data["size-files-histogram"]])
        minfmt = "%" + str(maxlen) + "d"
        maxfmt = "%-" + str(maxlen) + "d"
        countfmt = "%-" + str(maxcountlen) + "d"
        linefmt = minfmt + "-" + maxfmt + " : " + countfmt + "    %s"
        for (minsize, maxsize, count) in data["size-files-histogram"]:
            if prevmax is not None and minsize != prevmax+1:
                print >>stdout, " "*(maxlen-1) + "..."
            prevmax = maxsize
            print >>stdout, linefmt % (minsize, maxsize, count,
                                       abbreviate_space_both(maxsize))

def stats(options):
    return StatsGrabber().run(options)
