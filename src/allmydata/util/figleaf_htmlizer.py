#! /usr/bin/env python
import sys
import pickle
import figleaf
import os
import re

from twisted.python import usage

class RenderOptions(usage.Options):
    optParameters = [
        ("exclude-patterns", "x", None, "file containing regexp patterns to exclude"),
        ("output-directory", "d", "html", "Directory for HTML output"),
        ("root", "r", None, "only pay attention to modules under this directory"),
        ("old-coverage", "o", None, "figleaf pickle from previous build"),
        ]

    def opt_root(self, value):
        self["root"] = os.path.abspath(value)
        if not self["root"].endswith("/"):
            self["root"] += "/"

    def parseArgs(self, *filenames):
        self.filenames = [".figleaf"]
        if filenames:
            self.filenames = list(filenames)

class Renderer:

    def run(self):
        self.opts = opts = RenderOptions()
        opts.parseOptions()

        ### load

        coverage = {}
        for filename in opts.filenames:
            d = figleaf.read_coverage(filename)
            coverage = figleaf.combine_coverage(coverage, d)

        if not coverage:
            sys.exit(-1)

        self.old_coverage = None
        if opts["old-coverage"]:
            try:
                f = open(opts["old-coverage"], "rb")
                self.old_coverage = pickle.load(f)
            except EnvironmentError:
                pass

        self.load_exclude_patterns(opts["exclude-patterns"])
        ### make directory
        self.prepare_reportdir(opts["output-directory"])
        self.report_as_html(coverage, opts["output-directory"], opts["root"])

    def load_exclude_patterns(self, f):
        self.exclude_patterns = []
        if not f:
            return
        for line in open(f, "r").readlines():
            line = line.rstrip()
            if line and not line.startswith('#'):
                self.exclude_patterns.append(re.compile(line))

    def prepare_reportdir(self, dirname='html'):
        try:
            os.mkdir(dirname)
        except OSError:                         # already exists
            pass

    def check_excludes(self, fn):
        for pattern in self.exclude_patterns:
            if pattern.search(fn):
                return True
        return False

    def make_display_filename(self, fn):
        root = self.opts["root"]
        if not root:
            return fn
        display_filename = fn[len(root):]
        assert not display_filename.startswith("/")
        assert display_filename.endswith(".py")
        display_filename = display_filename[:-3] # trim .py
        display_filename = display_filename.replace("/", ".")
        return display_filename

    def report_as_html(self, coverage, directory, root=None):
        ### now, output.

        keys = coverage.keys()
        info_dict = {}
        for k in keys:
            if self.check_excludes(k):
                continue
            if k.endswith('figleaf.py'):
                continue
            if not k.startswith("/"):
                continue
            if root and not k.startswith(root):
                continue

            display_filename = self.make_display_filename(k)
            info = self.process_file(k, display_filename, coverage)
            if info:
                info_dict[k] = info

        ### print a summary, too.
            #print info_dict

        info_dict_items = info_dict.items()

        def sort_by_pcnt(a, b):
            a_cmp = (-a[1][4], a[1][5])
            b_cmp = (-b[1][4], b[1][5])
            return cmp(a_cmp,b_cmp)

        def sort_by_uncovered(a, b):
            a_cmp = ( -(a[1][0] - a[1][1]), a[1][5])
            b_cmp = ( -(b[1][0] - b[1][1]), b[1][5])
            return cmp(a_cmp, b_cmp)

        def sort_by_delta(a, b):
            # files which lost coverage line should appear first, followed by
            # files which gained coverage
            a_cmp = (-a[1][3], -a[1][2], a[1][5])
            b_cmp = (-b[1][3], -b[1][2], b[1][5])
            return cmp(a_cmp, b_cmp)

        info_dict_items.sort(sort_by_uncovered)

        summary_lines = sum([ v[0] for (k, v) in info_dict_items])
        summary_cover = sum([ v[1] for (k, v) in info_dict_items])
        summary_added = sum([ v[2] for (k, v) in info_dict_items])
        summary_removed = sum([ v[3] for (k, v) in info_dict_items])
        summary_pcnt = 0
        if summary_lines:
            summary_pcnt = float(summary_cover) * 100. / float(summary_lines)
        self.summary = (summary_lines, summary_cover,
                        summary_added, summary_removed,
                        summary_pcnt)


        pcnts = [ float(v[1]) * 100. / float(v[0]) for (k, v) in info_dict_items if v[0] ]
        pcnt_90 = [ x for x in pcnts if x >= 90 ]
        pcnt_75 = [ x for x in pcnts if x >= 75 ]
        pcnt_50 = [ x for x in pcnts if x >= 50 ]

        stats_fp = open('%s/stats.out' % (directory,), 'w')
        self.write_stats(stats_fp, "total files: %d" % len(pcnts))
        self.write_stats(stats_fp, "total source lines: %d" % summary_lines)
        self.write_stats(stats_fp, "total covered lines: %d" % summary_cover)
        self.write_stats(stats_fp,
                         "total uncovered lines: %d" % (summary_lines - summary_cover))
        if self.old_coverage is not None:
            self.write_stats(stats_fp, "lines added: %d" % summary_added)
            self.write_stats(stats_fp, "lines removed: %d" % summary_removed)
        self.write_stats(stats_fp,
                         "total coverage percentage: %.1f" % summary_pcnt)
        stats_fp.close()

        ## index.html
        index_fp = open('%s/index.html' % (directory,), 'w')
        # summary info
        index_fp.write('<title>figleaf code coverage report</title>\n')
        index_fp.write('<h2>Summary</h2> %d files total: %d files &gt; '
                       '90%%, %d files &gt; 75%%, %d files &gt; 50%%<p>'
                       % (len(pcnts), len(pcnt_90),
                          len(pcnt_75), len(pcnt_50)))

        # sorted by number of lines that aren't covered
        index_fp.write('<h3>Sorted by Lines Uncovered</h3>\n')
        self.emit_table(index_fp, info_dict_items, show_totals=True)

        if self.old_coverage is not None:
            index_fp.write('<h3>Sorted by Coverage Added/Lost</h3>\n')
            info_dict_items.sort(sort_by_delta)
            self.emit_table(index_fp, info_dict_items, show_totals=False)

        # sorted by module name
        index_fp.write('<h3>Sorted by Module Name (alphabetical)</h3>\n')
        info_dict_items.sort()
        self.emit_table(index_fp, info_dict_items, show_totals=False)

        index_fp.close()

        return len(info_dict)

    def process_file(self, k, display_filename, coverage):

        try:
            pyfile = open(k)
        except IOError:
            return

        source_lines = figleaf.get_lines(pyfile)

        have_old_coverage = False
        if self.old_coverage and k in self.old_coverage:
            have_old_coverage = True
            old_coverage = self.old_coverage[k]

        # ok, got all the info.  now annotate file ==> html.

        covered = coverage[k]
        n_covered = n_lines = 0
        n_added = n_removed = 0

        pyfile = open(k)
        output = []
        for i, line in enumerate(pyfile):
            i += 1 # coverage info is 1-based

            if i in covered:
                color = "green"
                n_covered += 1
                n_lines += 1
            elif i in source_lines:
                color = "red"
                n_lines += 1
            else:
                color = "black"

            delta = " "
            if have_old_coverage:
                if i in covered and i not in old_coverage:
                    delta = "+"
                    n_added += 1
                elif i in old_coverage and i not in covered:
                    delta = "-"
                    n_removed += 1

            line = self.escape_html(line.rstrip())
            output.append('<font color="%s">%s%4d. %s</font>' %
                          (color, delta, i, line.rstrip()))

        try:
            pcnt = n_covered * 100. / n_lines
        except ZeroDivisionError:
            pcnt = 0

        html_outfile = self.make_html_filename(display_filename)
        directory = self.opts["output-directory"]
        html_outfp = open(os.path.join(directory, html_outfile), 'w')
        html_outfp.write('source file: <b>%s</b><br>\n' % (k,))
        html_outfp.write('file stats: <b>%d lines, %d executed: %.1f%% covered</b><br>\n' % (n_lines, n_covered, pcnt))
        if have_old_coverage:
            html_outfp.write('coverage versus previous test: <b>%d lines added, %d lines removed</b><br>\n'
                             % (n_added, n_removed))

        html_outfp.write('<pre>\n')
        for line in output:
            html_outfp.write(line + "\n")
        html_outfp.write('</pre>\n')
        html_outfp.close()

        return (n_lines, n_covered, n_added, n_removed, pcnt, display_filename)

    def emit_table(self, index_fp, items, show_totals):
        have_old_coverage = self.old_coverage is not None
        if have_old_coverage:
            index_fp.write('<table border=1><tr><th>Filename</th>'
                           '<th># lines</th><th># covered</th>'
                           '<th># uncovered</th>'
                           '<th># added</th>'
                           '<th># removed</th>'
                           '<th>% covered</th></tr>\n')
        else:
            index_fp.write('<table border=1><tr><th>Filename</th>'
                           '<th># lines</th><th># covered</th>'
                           '<th># uncovered</th>'
                           '<th>% covered</th></tr>\n')
        if show_totals:
            (summary_lines, summary_cover, summary_pcnt,
             summary_added, summary_removed) = self.summary
            if have_old_coverage:
                index_fp.write('<tr><td><b>totals:</b></td>'
                               '<td><b>%d</b></td>' # lines
                               '<td><b>%d</b></td>' # cover
                               '<td><b>%d</b></td>' # uncover
                               '<td><b>%d</b></td>' # added
                               '<td><b>%d</b></td>' # removed
                               '<td><b>%.1f%%</b></td>'
                               '</tr>'
                               '<tr></tr>\n'
                               % (summary_lines, summary_cover,
                                  (summary_lines - summary_cover),
                                  summary_added, summary_removed,
                                  summary_pcnt,))
            else:
                index_fp.write('<tr><td><b>totals:</b></td>'
                               '<td><b>%d</b></td>'
                               '<td><b>%d</b></td>'
                               '<td><b>%d</b></td>'
                               '<td><b>%.1f%%</b></td>'
                               '</tr>'
                               '<tr></tr>\n'
                               % (summary_lines, summary_cover,
                                  (summary_lines - summary_cover),
                                  summary_pcnt,))

        for filename, stuff in items:
            self.emit_table_row(index_fp, stuff)

        index_fp.write('</table>\n')

    def emit_table_row(self, index_fp, info):
        (n_lines, n_covered, n_added, n_removed,
         percent_covered, display_filename) = info
        html_outfile = self.make_html_filename(display_filename)

        if self.old_coverage is not None:
            index_fp.write('<tr><td><a href="./%s">%s</a></td>'
                           '<td>%d</td>' # lines
                           '<td>%d</td>' # covered
                           '<td>%d</td>' # uncovered
                           '<td>%d</td>' # added
                           '<td>%d</td>' # removed
                           '<td>%.1f</td>'
                           '</tr>\n'
                           % (html_outfile, display_filename, n_lines,
                              n_covered, (n_lines - n_covered),
                              n_added, n_removed,
                              percent_covered,))
        else:
            index_fp.write('<tr><td><a href="./%s">%s</a></td>'
                           '<td>%d</td>'
                           '<td>%d</td>'
                           '<td>%d</td>'
                           '<td>%.1f</td>'
                           '</tr>\n'
                           % (html_outfile, display_filename, n_lines,
                              n_covered, (n_lines - n_covered),
                              percent_covered,))

    def make_html_filename(self, orig):
        return orig + ".html"

    def escape_html(self, s):
        s = s.replace("&", "&amp;")
        s = s.replace("<", "&lt;")
        s = s.replace(">", "&gt;")
        s = s.replace('"', "&quot;")
        return s

    def write_stats(self, stats_fp, line):
        stats_fp.write(line + "\n")
        print line

def main():
    r = Renderer()
    r.run()
