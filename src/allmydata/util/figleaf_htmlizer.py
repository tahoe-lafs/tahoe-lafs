#! /usr/bin/env python
import sys
import figleaf
from cPickle import load
import os
import re

from optparse import OptionParser

import logging
logging.basicConfig(level=logging.DEBUG)

logger = logging.getLogger('figleaf.htmlizer')

def read_exclude_patterns(f):
	if not f:
		return []
	exclude_patterns = []

	fp = open(f)
	for line in fp:
		line = line.rstrip()
		if line and not line.startswith('#'):
			pattern = re.compile(line)
		exclude_patterns.append(pattern)

	return exclude_patterns

def report_as_html(coverage, directory, exclude_patterns=[], root=None):
	### now, output.

	keys = coverage.keys()
	info_dict = {}
	for k in keys:
		skip = False
		for pattern in exclude_patterns:
			if pattern.search(k):
				logger.debug('SKIPPING %s -- matches exclusion pattern' % k)
				skip = True
				break

		if skip:
			continue

		if k.endswith('figleaf.py'):
			continue

                display_filename = k
                if root:
                        if not k.startswith(root):
                                continue
                        display_filename = k[len(root):]
                        assert not display_filename.startswith("/")
                        assert display_filename.endswith(".py")
                        display_filename = display_filename[:-3] # trim .py
                        display_filename = display_filename.replace("/", ".")

                if not k.startswith("/"):
                        continue

		try:
			pyfile = open(k)
#            print 'opened', k
		except IOError:
			logger.warning('CANNOT OPEN: %s' % k)
			continue

		try:
			lines = figleaf.get_lines(pyfile)
		except KeyboardInterrupt:
			raise
		except Exception, e:
			pyfile.close()
			logger.warning('ERROR: %s %s' % (k, str(e)))
			continue

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

			line = escape_html(line.rstrip())
			output.append('<font color="%s">%4d. %s</font>' % (color, i, line.rstrip()))

		try:
			pcnt = n_covered * 100. / n_lines
		except ZeroDivisionError:
			pcnt = 100
		info_dict[k] = (n_lines, n_covered, pcnt, display_filename)

		html_outfile = make_html_filename(display_filename)
		html_outfp = open(os.path.join(directory, html_outfile), 'w')
		html_outfp.write('source file: <b>%s</b><br>\n' % (k,))
		html_outfp.write('file stats: <b>%d lines, %d executed: %.1f%% covered</b>\n' % (n_lines, n_covered, pcnt))

		html_outfp.write('<pre>\n')
		html_outfp.write("\n".join(output))
		html_outfp.close()

	### print a summary, too.

	info_dict_items = info_dict.items()

	def sort_by_pcnt(a, b):
		a = a[1][2]
		b = b[1][2]

		return -cmp(a,b)
	info_dict_items.sort(sort_by_pcnt)

	summary_lines = sum([ v[0] for (k, v) in info_dict_items])
	summary_cover = sum([ v[1] for (k, v) in info_dict_items])

	summary_pcnt = 100
	if summary_lines:
		summary_pcnt = float(summary_cover) * 100. / float(summary_lines)


	pcnts = [ float(v[1]) * 100. / float(v[0]) for (k, v) in info_dict_items if v[0] ]
	pcnt_90 = [ x for x in pcnts if x >= 90 ]
	pcnt_75 = [ x for x in pcnts if x >= 75 ]
	pcnt_50 = [ x for x in pcnts if x >= 50 ]

	index_fp = open('%s/index.html' % (directory,), 'w')
	index_fp.write('<title>figleaf code coverage report</title>\n')
	index_fp.write('<h2>Summary</h2> %d files total: %d files &gt; 90%%, %d files &gt; 75%%, %d files &gt; 50%%<p>' % (len(pcnts), len(pcnt_90), len(pcnt_75), len(pcnt_50)))
	index_fp.write('<table border=1><tr><th>Filename</th><th># lines</th><th># covered</th><th>% covered</th></tr>\n')
	index_fp.write('<tr><td><b>totals:</b></td><td><b>%d</b></td><td><b>%d</b></td><td><b>%.1f%%</b></td></tr><tr></tr>\n' % (summary_lines, summary_cover, summary_pcnt,))

	for filename, (n_lines, n_covered, percent_covered, display_filename) in info_dict_items:
		html_outfile = make_html_filename(display_filename)

		index_fp.write('<tr><td><a href="./%s">%s</a></td><td>%d</td><td>%d</td><td>%.1f</td></tr>\n' % (html_outfile, display_filename, n_lines, n_covered, percent_covered,))

	index_fp.write('</table>\n')
	index_fp.close()

	logger.info('reported on %d file(s) total\n' % len(info_dict))
	return len(info_dict)

def prepare_reportdir(dirname='html'):
	try:
		os.mkdir(dirname)
	except OSError:                         # already exists
		pass

def make_html_filename(orig):
        return orig + ".html"

def escape_html(s):
	s = s.replace("&", "&amp;")
	s = s.replace("<", "&lt;")
	s = s.replace(">", "&gt;")
	s = s.replace('"', "&quot;")
	return s

def main():
	###

	option_parser = OptionParser()

	option_parser.add_option('-x', '--exclude-patterns', action="store",
                                 dest="exclude_patterns_file",
                                 help="file containing regexp patterns to exclude")

	option_parser.add_option('-d', '--output-directory', action='store',
				 dest="output_dir",
				 default = "html",
				 help="directory for HTML output")
        option_parser.add_option('-r', '--root', action="store",
                                 dest="root",
                                 default=None,
                                 help="only pay attention to modules under this directory")

	option_parser.add_option('-q', '--quiet', action='store_true', dest='quiet', help='Suppress all but error messages')

	(options, args) = option_parser.parse_args()

	if options.quiet:
		logging.disable(logging.DEBUG)

        if options.root:
                options.root = os.path.abspath(options.root)
                if options.root[-1] != "/":
                        options.root = options.root + "/"

	### load

	if not args:
		args = ['.figleaf']

	coverage = {}
	for filename in args:
		logger.debug("loading coverage info from '%s'\n" % (filename,))
		d = figleaf.read_coverage(filename)
		coverage = figleaf.combine_coverage(coverage, d)

	if not coverage:
		logger.warning('EXITING -- no coverage info!\n')
		sys.exit(-1)

	### make directory
	prepare_reportdir(options.output_dir)
	report_as_html(coverage, options.output_dir,
                       read_exclude_patterns(options.exclude_patterns_file),
                       options.root)

