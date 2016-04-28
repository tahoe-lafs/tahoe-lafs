#!/usr/bin/env python

# Run this as "./graph-deps.py ." from your source tree, then open out.png .
# You can also use a PyPI package name, e.g. "./graph-deps.py tahoe-lafs".
#
# This builds all necessary wheels for your project (in a tempdir), scans
# them to learn their inter-dependencies, generates a DOT-format graph
# specification, then runs the "dot" program (from the "graphviz" package) to
# turn this into a PNG image.

# To hack on this script (e.g. change the way it generates DOT) without
# re-building the wheels each time, set --wheeldir= to some not-existent
# path. It will write the wheels to that directory instead of a tempdir. The
# next time you run it, if --wheeldir= points to a directory, it will read
# the wheels from there.

# To hack on the DOT output without re-running this script, add --write-dot,
# which will cause it to write "out.dot". Edit that file, then run "dot -Tpng
# out.dot >out.png" to re-render the graph.

# Install 'click' first. I run this with py2, but py3 might work too, if the
# wheels can be built with py3.

from __future__ import print_function, unicode_literals
import os, sys, subprocess, json, tempfile, zipfile, io, re, itertools
import email.parser
from pprint import pprint
import click

all_packages = {} # name -> version
all_reqs = {} # name -> specs
all_pure = set()

# 1: build a local directory of wheels for the given target
# pip wheel --wheel-dir=tempdir sys.argv[1]
def build_wheels(target, wheeldir):
    print("-- building wheels for '%s' in %s" % (target, wheeldir))
    pip = subprocess.Popen(["pip", "wheel", "--wheel-dir", wheeldir, target],
                           stdout=subprocess.PIPE)
    stdout = pip.communicate()[0]
    if pip.returncode != 0:
        sys.exit(pip.returncode)
    # 'pip wheel .' starts with "Processing /path/to/." but ends with
    # "Successfully built PKGNAME". 'pip wheel PKGNAME' start with
    # "Collecting PKGNAME" but ends with e.g. "Skipping foo, due to already
    # being wheel."
    lines = stdout.decode("utf-8").splitlines()
    if lines[0].startswith("Collecting "):
        root_pkgname = lines[0].split()[-1]
    elif lines[-1].startswith("Successfully built "):
        root_pkgname = lines[-1].split()[-1]
    else:
        print("Unable to figure out root package name")
        print("'pip wheel %s' output is:" % target)
        print(stdout)
        sys.exit(1)
    with open(os.path.join(wheeldir, "root_pkgname"), "w") as f:
        f.write(root_pkgname+"\n")

def get_root_pkgname(wheeldir):
    with open(os.path.join(wheeldir, "root_pkgname"), "r") as f:
        return f.read().strip()

# 2: for each wheel, find the *.dist-info file, find metadata.json inside
# that, extract metadata.run_requires[0].requires

def add(name, version, extras, reqs, raw):
    if set(reqs) - set([None]) - set(extras):
        print("um, %s metadata has mismatching extras/reqs" % name)
        pprint(extras)
        pprint(reqs)
        print("raw data:")
        pprint(raw)
        raise ValueError
    if None not in reqs:
        print("um, %s has no reqs" % name)
        print("raw data:")
        pprint(raw)
        raise ValueError
    all_packages[name] = version
    all_reqs[name] = reqs

def parse_metadata_json(f):
    md = json.loads(f.read().decode("utf-8"))
    name = md["name"].lower()
    version = md["version"]
    try:
        reqs = {None: []} # extra_name/None -> [specs]
        if "run_requires" in md:
            for r in md["run_requires"]:
                reqs[r.get("extra", None)] = r["requires"]
        # this package provides the following extras
        extras = md.get("extras", [])
        #for e in extras:
        #    if e not in reqs:
        #        reqs[e] = []
    except KeyError:
        print("error in '%s'" % name)
        pprint(md)
        raise
    add(name, version, extras, reqs, md)
    return name

def parse_METADATA(f):
    data = f.read().decode("utf-8")
    md = email.parser.Parser().parsestr(data)

    name = md.get_all("Name")[0].lower()
    version = md.get_all("Version")[0]
    reqs = {None: []}
    for req in md.get_all("Requires-Dist") or []: # untested
        pieces = [p.strip() for p in req.split(";")]
        spec = pieces[0]
        extra = None
        if len(pieces) > 1:
            mo = re.search(r"extra == '(\w+)'", pieces[1])
            if mo:
                extra = mo.group(1)
        if extra not in reqs:
            reqs[extra] = []
        reqs[extra].append(spec)
    extras = md.get_all("Provides-Extra") or [] # untested
    add(name, version, extras, reqs, data)
    return name

def parse_wheels(wheeldir):
    for fn in os.listdir(wheeldir):
        if not fn.endswith(".whl"):
            continue
        zf = zipfile.ZipFile(os.path.join(wheeldir, fn))
        zfnames = zf.namelist()
        mdfns = [n for n in zfnames if n.endswith(".dist-info/metadata.json")]
        if mdfns:
            name = parse_metadata_json(zf.open(mdfns[0]))
        else:
            mdfns = [n for n in zfnames if n.endswith(".dist-info/METADATA")]
            if mdfns:
                name = parse_METADATA(zf.open(mdfns[0]))
            else:
                print("no metadata for", fn)
                continue
        is_pure = False
        wheel_fns = [n for n in zfnames if n.endswith(".dist-info/WHEEL")]
        if wheel_fns:
            with zf.open(wheel_fns[0]) as wheel:
                for line in wheel:
                    if line.lower().rstrip() == b"root-is-purelib: true":
                        is_pure = True
        if is_pure:
            all_pure.add(name)
    return get_root_pkgname(wheeldir)

# 3: emit a .dot file with a graph of all the dependencies

def dot_name(name, extra):
    # the 'dot' format enforces C identifier syntax on node names
    assert name.lower() == name, name
    name = "%s__%s" % (name, extra)
    return name.replace("-", "_").replace(".", "_")

def parse_spec(spec):
    # turn "twisted[tls] (>=16.0.0)" into "twisted"
    pieces = spec.split()
    name_and_extras = pieces[0]
    paren_constraint = pieces[1] if len(pieces) > 1 else ""
    if "[" in name_and_extras:
        name = name_and_extras[:name_and_extras.find("[")]
        extras_bracketed = name_and_extras[name_and_extras.find("["):]
        extras = extras_bracketed.strip("[]").split(",")
    else:
        name = name_and_extras
        extras = []
    return name.lower(), extras, paren_constraint

def format_attrs(**kwargs):
    # return "", or "[attr=value attr=value]"
    if not kwargs or all([not(v) for v in kwargs.values()]):
        return ""
    def escape(s):
        return s.replace('\n', r'\n').replace('"', r'\"')
    pieces = ['%s="%s"' % (k, escape(kwargs[k]))
              for k in sorted(kwargs)
              if kwargs[k]]
    body = " ".join(pieces)
    return "[%s]" % body

# We draw a node for each wheel. When one of the inbound dependencies asks
# for an extra, we assign that (target, extra) pair a color. We draw outbound
# links for all non-extra dependencies in black. If something asked the
# target for an extra, we also draw links for the extra deps using the
# assigned color.

COLORS = itertools.cycle(["green", "blue", "red", "purple"])
extras_to_show = {} # maps (target, extraname) -> colorname

def add_extra_to_show(targetname, extraname):
    key = (targetname, extraname)
    if key not in extras_to_show:
        extras_to_show[key] = next(COLORS)

_scanned = set()
def scan(name, extra=None, path=""):
    dupkey = (name, extra)
    if dupkey in _scanned:
        #print("SCAN-SKIP %s %s[%s]" % (path, name, extra))
        return
    _scanned.add(dupkey)
    #print("SCAN %s %s[%s]" % (path, name, extra))
    add_extra_to_show(name, extra)
    for spec in all_reqs[name][extra]:
        #print("-", spec)
        dep_name, dep_extras, dep_constraint = parse_spec(spec)
        #print("--", dep_name, dep_extras)
        children = set(dep_extras)
        children.add(None)
        for dep_extra in children:
            scan(dep_name, dep_extra,
                 path=path+"->%s[%s]" % (dep_name, dep_extra))

def generate_dot():
    f = io.StringIO()
    f.write("digraph {\n")
    for name, extra in extras_to_show.keys():
        version = all_packages[name]
        if extra:
            label = "%s[%s]\n%s" % (name, extra, version)
        else:
            label = "%s\n%s" % (name, version)
        color = None
        if name not in all_pure:
            color = "red"
        f.write('%s %s\n' % (dot_name(name, extra),
                             format_attrs(label=label, color=color)))

    for (source, extra), color in extras_to_show.items():
        if extra:
            f.write('%s -> %s [weight="50" style="dashed"]\n' %
                    (dot_name(source, extra),
                     dot_name(source, None)))
        specs = all_reqs[source][extra]
        for spec in specs:
            reqname, reqextras, paren_constraint = parse_spec(spec)
            #extras_bracketed = "[%s]" % ",".join(extras) if extras else ""
            #edge_label = " ".join([p for p in [extras_bracketed,
            #                                   paren_constraint] if p])
            assert None not in reqextras
            if not reqextras:
                reqextras = [None]
            for reqextra in reqextras:
                edge_label = ""
                if extra:
                    edge_label += "(%s[%s] wants)\n" % (source, extra)
                edge_label += spec
                style = "bold" if reqextra else "solid"
                f.write('%s -> %s %s\n' % (dot_name(source, extra),
                                           dot_name(reqname, reqextra),
                                           format_attrs(label=edge_label,
                                                        fontcolor=color,
                                                        style=style,
                                                        color=color)))
    f.write("}\n")
    return f

# 4: convert to .png
def dot_to_png(f, png_fn):
    png = open(png_fn, "wb")
    dot = subprocess.Popen(["dot", "-Tpng"], stdin=subprocess.PIPE, stdout=png)
    dot.communicate(f.getvalue().encode("utf-8"))
    if dot.returncode != 0:
        sys.exit(dot.returncode)
    png.close()
    print("wrote graph to %s" % png_fn)

@click.command()
@click.argument("target")
@click.option("--wheeldir", default=None, type=str)
@click.option("--write-dot/--no-write-dot", default=False)
def go(target, wheeldir, write_dot):
    if wheeldir:
        if os.path.isdir(wheeldir):
            print("loading wheels from", wheeldir)
            root_pkgname = parse_wheels(wheeldir)
        else:
            assert not os.path.exists(wheeldir)
            print("loading wheels from", wheeldir)
            build_wheels(target, wheeldir)
            root_pkgname = parse_wheels(wheeldir)
    else:
        wheeldir = tempfile.mkdtemp()
        build_wheels(target, wheeldir)
        root_pkgname = parse_wheels(wheeldir)
    print("root package:", root_pkgname)

    # parse the requirement specs (which look like "Twisted[tls] (>=13.0.0)")
    # enough to identify the package name
    pprint(all_packages)
    pprint(all_reqs)
    print("pure:", " ".join(sorted(all_pure)))

    for name in all_packages.keys():
        extras_to_show[(name, None)] = "black"

    scan(root_pkgname)
    f = generate_dot()

    if write_dot:
        with open("out.dot", "w") as dotf:
            dotf.write(f.getvalue())
        print("wrote DOT to out.dot")
    dot_to_png(f, "out.png")

    return 0

if __name__ == "__main__":
    go()
