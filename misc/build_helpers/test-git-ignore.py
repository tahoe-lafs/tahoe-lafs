#!/usr/bin/env python

import sys
from subprocess import Popen, PIPE

cmd = ["git", "status", "--porcelain"]
p = Popen(cmd, stdout=PIPE)
output = p.communicate()[0]
print output
if output == "":
    sys.exit(0)
sys.exit(1)


