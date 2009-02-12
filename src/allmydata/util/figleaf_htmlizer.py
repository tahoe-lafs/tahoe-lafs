#! /usr/bin/env python
import sys
import figleaf
import os
import re

from twisted.python import usage

class RenderOptions(usage.Options):
    optParameters = [
        ("exclude-patterns", "x", None, "file containing regexp patterns to exclude"),
        ("output-directory", "d", "html", "Directory for HTML output"),
        ("root", "r", None, "only pay attention to modules under this directory"),
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

            display_filename = self.make_display_filename(k)
            info = self.process_file(k, display_filename, coverage)
            if info:
                info_dict[k] = info

        ### print a summary, too.
            #print info_dict

        info_dict_items = info_dict.items()

        def sort_by_pcnt(a, b):
            a = a[1][2]
            b = b[1][2]
            return -cmp(a,b)

        def sort_by_uncovered(a, b):
            a_uncovered = a[1][0] - a[1][1]
            b_uncovered = b[1][0] - b[1][1]
            return -cmp(a_uncovered, b_uncovered)

        info_dict_items.sort(sort_by_uncovered)

        summary_lines = sum([ v[0] for (k, v) in info_dict_items])
        summary_cover = sum([ v[1] for (k, v) in info_dict_items])

        summary_pcnt = 0
        if summary_lines:
            summary_pcnt = float(summary_cover) * 100. / float(summary_lines)


        pcnts = [ float(v[1]) * 100. / float(v[0]) for (k, v) in info_dict_items if v[0] ]
        pcnt_90 = [ x for x in pcnts if x >= 90 ]
        pcnt_75 = [ x for x in pcnts if x >= 75 ]
        pcnt_50 = [ x for x in pcnts if x >= 50 ]

        stats_fp = open('%s/stats.out' % (directory,), 'w')
        stats_fp.write("total files: %d\n" % len(pcnts))
        stats_fp.write("total source lines: %d\n" % summary_lines)
        stats_fp.write("total covered lines: %d\n" % summary_cover)
        stats_fp.write("total uncovered lines: %d\n" %
                       (summary_lines - summary_cover))
        stats_fp.write("total coverage percentage: %.1f\n" % summary_pcnt)
        stats_fp.close()

        ## index.html
        index_fp = open('%s/index.html' % (directory,), 'w')
        # summary info
        index_fp.write('<title>figleaf code coverage report</title>\n')
        index_fp.write('<h2>Summary</h2> %d files total: %d files &gt; '
                       '90%%, %d files &gt; 75%%, %d files &gt; 50%%<p>'
                       % (len(pcnts), len(pcnt_90),
                          len(pcnt_75), len(pcnt_50)))

        def emit_table(items, show_totals):
            index_fp.write('<table border=1><tr><th>Filename</th>'
                           '<th># lines</th><th># covered</th>'
                           '<th># uncovered</th>'
                           '<th>% covered</th></tr>\n')
            if show_totals:
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
                (n_lines, n_covered, percent_covered, display_filename) = stuff
                html_outfile = self.make_html_filename(display_filename)

                index_fp.write('<tr><td><a href="./%s">%s</a></td>'
                               '<td>%d</td><td>%d</td><td>%d</td><td>%.1f</td>'
                               '</tr>\n'
                               % (html_outfile, display_filename, n_lines,
                                  n_covered, (n_lines - n_covered),
                                  percent_covered,))

        index_fp.write('</table>\n')

        # sorted by number of lines that aren't covered
        index_fp.write('<h3>Sorted by Lines Uncovered</h3>\n')
        emit_table(info_dict_items, True)

        # sorted by module name
        index_fp.write('<h3>Sorted by Module Name (alphabetical)</h3>\n')
        info_dict_items.sort()
        emit_table(info_dict_items, False)

        index_fp.close()

        return len(info_dict)

    def process_file(self, k, display_filename, coverage):

        try:
            pyfile = open(k)
        except IOError:
            return

        lines = figleaf.get_lines(pyfile)

        # ok, got all the info.  now annotate file ==> html.

        covered = coverage[k]
        n_covered = n_lines = 0

        pyfile = open(k)
        output = []
        for i, line in enumerate(pyfile):
            is_covered = False
            is_line = False

            i += 1

            if i in covered:
                is_covered = True

                n_covered += 1
                n_lines += 1
            elif i in lines:
                is_line = True

                n_lines += 1

            color = 'black'
            if is_covered:
                color = 'green'
            elif is_line:
                color = 'red'

            line = self.escape_html(line.rstrip())
            output.append('<font color="%s">%4d. %s</font>' % (color, i, line.rstrip()))

        try:
            pcnt = n_covered * 100. / n_lines
        except ZeroDivisionError:
            pcnt = 0

        html_outfile = self.make_html_filename(display_filename)
        directory = self.opts["output-directory"]
        html_outfp = open(os.path.join(directory, html_outfile), 'w')
        html_outfp.write('source file: <b>%s</b><br>\n' % (k,))
        html_outfp.write('file stats: <b>%d lines, %d executed: %.1f%% covered</b>\n' % (n_lines, n_covered, pcnt))

        html_outfp.write('<pre>\n')
        html_outfp.write("\n".join(output))
        html_outfp.close()

        return (n_lines, n_covered, pcnt, display_filename)

    def make_html_filename(self, orig):
        return orig + ".html"

    def escape_html(self, s):
        s = s.replace("&", "&amp;")
        s = s.replace("<", "&lt;")
        s = s.replace(">", "&gt;")
        s = s.replace('"', "&quot;")
        return s

def main():
    r = Renderer()
    r.run()
