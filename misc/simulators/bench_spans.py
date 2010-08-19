from pyutil import benchutil

from allmydata.util.spans import DataSpans

import re, sys

DUMP_S='_received spans trace .dump()'
GET_R=re.compile('_received spans trace .get\(([0-9]*), ([0-9]*)\)')
POP_R=re.compile('_received spans trace .pop\(([0-9]*), ([0-9]*)\)')
REMOVE_R=re.compile('_received spans trace .remove\(([0-9]*), ([0-9]*)\)')
GET_SPANS_S='_received spans trace .get_spans()'
ADD_R=re.compile('_received spans trace .add\(([0-9]*), len=([0-9]*)\)')
INIT_S='_received spans trace = DataSpans'

class B(object):
    def __init__(self, inf):
        self.inf = inf

    def init(self, N):
        self.s = DataSpans()
        # self.stats = {}

    def run(self, N):
        count = 0
        inline = self.inf.readline()

        while count < N and inline != '':
            if DUMP_S in inline:
                self.s.dump()
                # self.stats['dump'] = self.stats.get('dump', 0) + 1
            elif GET_SPANS_S in inline:
                self.s.get_spans()
                # self.stats['get_spans'] = self.stats.get('get_spans', 0) + 1
            elif ADD_R.search(inline):
                mo = ADD_R.search(inline)
                start = int(mo.group(1))
                length = int(mo.group(2))
                self.s.add(start, 'x'*length)
                # self.stats['add'] = self.stats.get('add', 0) + 1
            elif GET_R.search(inline):
                mo = GET_R.search(inline)
                start = int(mo.group(1))
                length = int(mo.group(2))
                self.s.get(start, length)
                # self.stats['get'] = self.stats.get('get', 0) + 1
            elif REMOVE_R.search(inline):
                mo = REMOVE_R.search(inline)
                start = int(mo.group(1))
                length = int(mo.group(2))
                self.s.remove(start, length)
                # self.stats['remove'] = self.stats.get('remove', 0) + 1
            elif POP_R.search(inline):
                mo = POP_R.search(inline)
                start = int(mo.group(1))
                length = int(mo.group(2))
                self.s.pop(start, length)
                # self.stats['pop'] = self.stats.get('pop', 0) + 1
            elif INIT_S in inline:
                pass
            else:
                print "Warning, didn't recognize this line: %r" % (inline,)
            count += 1
            inline = self.inf.readline()

        # print self.stats

benchutil.print_bench_footer(UNITS_PER_SECOND=1000000)
print "(microseconds)"

for N in [600, 6000, 60000]:
    b = B(open(sys.argv[1], 'rU'))
    print "%7d" % N,
    benchutil.rep_bench(b.run, N, b.init, UNITS_PER_SECOND=1000000)

