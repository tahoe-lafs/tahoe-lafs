This package provides an "erasure code", or "forward error correction code".
It is licensed under the GNU General Public License (see the COPYING file for
details).

The most widely known example of an erasure code is the RAID-5 algorithm which
makes it so that in the event of the loss of any one hard drive, the stored
data can be completely recovered.  The algorithm in the pyfec package has a
similar effect, but instead of recovering from the loss of any one element, it
can be parameterized to choose in advance the number of elements whose loss it
can recover from.

This package is largely based on the old "fec" library by Luigi Rizzo et al.,
which is a simple, fast, mature, and optimized implementation of erasure
coding.  The pyfec package makes several changes from the original "fec"
package, including addition of the Python API, refactoring of the C API to be
faster (for the way that I use it, at least), and a few clean-ups and
micro-optimizations of the core code itself.

This package performs two operations, encoding and decoding.  Encoding takes
some input data and expands its size by producing extra "check blocks".
Decoding takes some blocks -- any combination of original blocks of data (also
called "primary shares") and check blocks (also called "secondary shares"), and
produces the original data.

The encoding is parameterized by two integers, k and m.  m is the total number
of shares produced, and k is how many of those shares are necessary to
reconstruct the original data.  m is required to be at least 1 and at most 255,
and k is required to be at least 1 and at most m.  (Note that when k == m then
there is no point in doing erasure coding.)

Note that each "primary share" is a segment of the original data, so its size
is 1/k'th of the size of original data, and each "secondary share" is of the
same size, so the total space used by all the shares is about m/k times the
size of the original data.

The decoding step requires as input k of the shares which were produced by the
encoding step.  The decoding step produces as output the data that was earlier
input to the encoding step.

This package also includes a Python interface.  See the Python docstrings for
usage details.

See also the filefec.py module which has a utility function for efficiently
reading a file and encoding it piece by piece.

Beware of a "gotcha" that can result from the combination of mutable buffers
and the fact that pyfec never makes an unnecessary data copy.  That is:
whenever one of the shares produced from a call to encode() or decode() has the
same contents as one of the shares passed as input, then pyfec will return as
output a pointer (in the C API) or a Python reference (in the Python API) to
the object which was passed to it as input.  This is efficient as it avoids
making an unnecessary copy of the data.  But if the object which was passed as
input is mutable and if that object is mutated after the call to pyfec returns,
then the result from pyfec -- which is just a reference to that same object --
will also be mutated.  This subtlety is the price you pay for avoiding data
copying.  If you don't want to have to worry about this, then simply use
immutable objects (e.g. Python strings) to hold the data that you pass to
pyfec.

Enjoy!

Zooko Wilcox-O'Hearn

