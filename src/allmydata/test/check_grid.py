#! /usr/bin/python

"""
Test an existing Tahoe grid, both to see if the grid is still running and to
see if the client is still compatible with it. This script is suitable for
running from a periodic monitoring script, perhaps by an hourly cronjob.

This script uses a pre-established client node (configured to connect to the
grid being tested) and a pre-established directory (stored as the 'testgrid:'
alias in that client node's aliases file). It then performs a number of
uploads and downloads to exercise compatibility in various directions (new
client vs old data).

This script expects that the client node will be running before the script
starts.

To set up the client node, do the following:

  tahoe create-client DIR
  touch DIR/no_storage
  populate DIR/introducer.furl
  tahoe start DIR
  tahoe -d DIR add-alias testgrid `tahoe -d DIR mkdir`
  pick a 10kB-ish test file, compute its md5sum
  tahoe -d DIR put FILE testgrid:old.MD5SUM
  tahoe -d DIR put FILE testgrid:recent.MD5SUM
  tahoe -d DIR put FILE testgrid:recentdir/recent.MD5SUM
  echo "" | tahoe -d DIR put --mutable testgrid:log
  echo "" | tahoe -d DIR put --mutable testgrid:recentlog

This script will perform the following steps (the kind of compatibility that
is being tested is in [brackets]):

 read old.* and check the md5sums [confirm that new code can read old files]
 read all recent.* files and check md5sums [read recent files]
 delete all recent.* files and verify they're gone [modify an old directory]
 read recentdir/recent.* files and check [read recent directory]
 delete recentdir/recent.* and verify [modify recent directory]
 delete recentdir and verify (keep the directory from growing unboundedly)
 mkdir recentdir
 upload random 10kB file to recentdir/recent.MD5SUM (prepare for next time)
 upload random 10kB file to recent.MD5SUM [new code can upload to old servers]
 append one-line timestamp to log [read/write old mutable files]
 append one-line timestamp to recentlog [read/write recent mutable files]
 delete recentlog
 upload small header to new mutable recentlog [create mutable files]

This script will also keep track of speeds and latencies and will write them
in a machine-readable logfile.

"""

