 * Intro

This package implements an "erasure code", or "forward error correction code".
It is licensed under the GNU General Public License (see the COPYING file for
details).

The most widely known example of an erasure code is the RAID-5 algorithm which
makes it so that in the event of the loss of any one hard drive, the stored
data can be completely recovered.  The algorithm in the pyfec package has a
similar effect, but instead of recovering from the loss of only a single
element, it can be parameterized to choose in advance the number of elements
whose loss it can tolerate.

This package is largely based on the old "fec" library by Luigi Rizzo et al.,
which is a simple, mature, and optimized implementation of erasure coding.  The
pyfec package makes several changes from the original "fec" package, including
addition of the Python API, refactoring of the C API to be faster (for the way
that I use it, at least), and a few clean-ups and micro-optimizations of the
core code itself.


 * Overview

This package performs two operations, encoding and decoding.  Encoding takes
some input data and expands its size by producing extra "check blocks", also
called "secondary shares".  Decoding takes some data -- any combination of
blocks of the original data (called "primary shares") and "secondary shares",
and produces the original data.

The encoding is parameterized by two integers, k and m.  m is the total number
of shares produced, and k is how many of those shares are necessary to
reconstruct the original data.  m is required to be at least 1 and at most 256,
and k is required to be at least 1 and at most m.

(Note that when k == m then there is no point in doing erasure coding -- it
degenerates to the equivalent of the Unix "split" utility which simply splits
the input into successive segments.  Similarly, when k == 1 it degenerates to
the equivalent of the unix "cp" utility -- each share is a complete copy of the
input data.)

Note that each "primary share" is a segment of the original data, so its size
is 1/k'th of the size of original data, and each "secondary share" is of the
same size, so the total space used by all the shares is m/k times the size of
the original data (plus some padding to fill out the last primary share to be
the same size as all the others).

The decoding step requires as input k of the shares which were produced by the
encoding step.  The decoding step produces as output the data that was earlier
input to the encoding step.


 * API

Each share is associated with "shareid".  The shareid of each primary share is
its index (starting from zero), so the 0'th share is the first primary share,
which is the first few bytes of the file, the 1'st share is the next primary
share, which is the next few bytes of the file, and so on.  The last primary
share has shareid k-1.  The shareid of each secondary share is an arbitrary
integer between k and 256 inclusive.  (When using the Python API, if you don't
specify which shareids you want for your secondary shares when invoking
encode(), then it will by default provide the shares with ids from k to m-1
inclusive.)

 ** C API

fec_encode() takes as input an array of k pointers, where each pointer points
to a memory buffer containing the input data (i.e., the i'th buffer contains
the i'th primary share).  There is also a second parameter which is an array of
the shareids of the secondary shares which are to be produced.  (Each element
in that array is required to be the shareid of a secondary share, i.e. it is
required to be >= k and < m.)

The output from fec_encode() is the requested set of secondary shares which are
written into output buffers provided by the caller.

fec_decode() takes as input an array of k pointers, where each pointer points
to a buffer containing a share.  There is also a separate input parameter which
is an array of shareids, indicating the shareid of each of the shares which is
being passed in.

The output from fec_decode() is the set of primary shares which were missing
from the input and had to be reconstructed.  These reconstructed shares are
written into putput buffers provided by the caller.

 ** Python API

encode() and decode() take as input a sequence of k buffers, where a "sequence"
is any object that implements the Python sequence protocol (such as a list or
tuple) and a "buffer" is any object that implements the Python buffer protocol
(such as a string or array).  The contents that are required to be present in
these buffers are the same as for the C API.

encode() also takes a list of desired shareids.  Unlike the C API, the Python
API accepts shareids of primary shares as well as secondary shares in its list
of desired shareids.  encode() returns a list of buffer objects which contain
the shares requested.  For each requested share which is a primary share, the
resulting list contains a reference to the apppropriate primary share from the
input list.  For each requested share which is a secondary share, the list
contains a newly created string object containing that share.

decode() also takes a list of integers indicating the shareids of the shares
being passed int.  decode() returns a list of buffer objects which contain all
of the primary shares of the original data (in order).  For each primary share
which was present in the input list, then the result list simply contains a
reference to the object that was passed in the input list.  For each primary
share which was not present in the input, the result list contains a newly
created string object containing that primary share.

Beware of a "gotcha" that can result from the combination of mutable data and
the fact that the Python API returns references to inputs when possible.

Returning references to its inputs is efficient since it avoids making an
unnecessary copy of the data, but if the object which was passed as input is
mutable and if that object is mutated after the call to pyfec returns, then the
result from pyfec -- which is just a reference to that same object -- will also
be mutated.  This subtlety is the price you pay for avoiding data copying.  If
you don't want to have to worry about this then you can simply use immutable
objects (e.g. Python strings) to hold the data that you pass to pyfec.


 * Utilities

See also the filefec.py module which has a utility function for efficiently
reading a file and encoding it piece by piece.


Enjoy!

Zooko Wilcox-O'Hearn
2007-01-27
San Francisco


On Peter's fancy Intel Mac laptop, it did about 6.21 million bytes per second from file.
On my old PowerPC G4 Mac laptop, it did around 1.3 million bytes per second from file.

