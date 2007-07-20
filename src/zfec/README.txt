 * Intro and Licence

This package implements an "erasure code", or "forward error correction
code".

It is offered under the GNU General Public License (v2 or later), with the
added permission that, if you become obligated to release a derived work
under this licence (as per section 2.b), you may delay the fulfillment of
this obligation for up to 12 months.  If you are obligated to release code 
under section 2.b of this licence, such code must be released under these 
same terms including the 12-month grace period clause.  See the COPYING 
file for details.

The most widely known example of an erasure code is the RAID-5 algorithm
which makes it so that in the event of the loss of any one hard drive, the
stored data can be completely recovered.  The algorithm in the zfec package
has a similar effect, but instead of recovering from the loss of only a
single element, it can be parameterized to choose in advance the number of
elements whose loss it can tolerate.

This package is largely based on the old "fec" library by Luigi Rizzo et al.,
which is a mature and optimized implementation of erasure coding.  The zfec
package makes several changes from the original "fec" package, including
addition of the Python API, refactoring of the C API to support zero-copy
operation, a few clean-ups and micro-optimizations of the core code itself, 
and the addition of a command-line tool named "zfec".


 * Installation

This package is managed with the "setuptools" package management tool.  To
build and install the package directly into your system, just run "python
./setup.py install".  If you prefer to keep the package limited to a specific
directory so that you can manage it yourself (perhaps by using the "GNU
stow") tool, then give it these arguments: "python ./setup.py install
--single-version-externally-managed
--record=${specificdirectory}/zfec-install.log --prefix=${specificdirectory}"


 * Community

The source is currently available via darcs on the web with the command:

darcs get http://allmydata.org/source/zfec

More information on darcs is available at http://darcs.net

Please join the zfec mailing list and submit patches:

<http://allmydata.org/cgi-bin/mailman/listinfo/zfec-dev>


 * Overview

This package performs two operations, encoding and decoding.  Encoding takes
some input data and expands its size by producing extra "check blocks", also
called "secondary blocks".  Decoding takes some data -- any combination of
blocks of the original data (called "primary blocks") and "secondary blocks",
and produces the original data.

The encoding is parameterized by two integers, k and m.  m is the total number
of blocks produced, and k is how many of those blocks are necessary to
reconstruct the original data.  m is required to be at least 1 and at most 256,
and k is required to be at least 1 and at most m.

(Note that when k == m then there is no point in doing erasure coding -- it
degenerates to the equivalent of the Unix "split" utility which simply splits
the input into successive segments.  Similarly, when k == 1 it degenerates to
the equivalent of the unix "cp" utility -- each block is a complete copy of the
input data.  The "zfec" command-line tool does not implement these degenerate 
cases.)

Note that each "primary block" is a segment of the original data, so its size
is 1/k'th of the size of original data, and each "secondary block" is of the
same size, so the total space used by all the blocks is m/k times the size of
the original data (plus some padding to fill out the last primary block to be
the same size as all the others).  In addition to the data contained in the 
blocks themselves there are also a few pieces of metadata which are necessary 
for later reconstruction.  Those pieces are: 1.  the value of K, 2.  the value 
of M,  3.  the sharenum of each block,  4.  the number of bytes of padding 
that were used.  The "zfec" command-line tool compresses these pieces of data 
and prepends them to the beginning of each share, so each the sharefile 
produced by the "zfec" command-line tool is between one and four bytes larger 
than the share data alone.

The decoding step requires as input k of the blocks which were produced by the
encoding step.  The decoding step produces as output the data that was earlier
input to the encoding step.


 * Command-Line Tool

The bin/ directory contains two Unix-style, command-line tools "zfec" and 
"zunfec".  Execute "zfec --help" or "zunfec --help" for usage instructions.

Note: a Unix-style tool like "zfec" does only one thing -- in this case
erasure coding -- and leaves other tasks to other tools.  Other Unix-style
tools that go well with zfec include "GNU tar" for archiving multiple files
and directories into one file, "rzip" or "lrzip" for compression, and "GNU
Privacy Guard" for encryption or "sha256sum" for integrity.  It is important
to do things in order: first archive, then compress, then either encrypt or
sha256sum, then erasure code.  Note that if GNU Privacy Guard is used for
privacy, then it will also ensure integrity, so the use of sha256sum is
unnecessary in that case.


 * Performance Measurements

On my Athlon 64 2.4 GHz workstation (running Linux), the "zfec" command-line
tool encoded a 160 MB file with m=100, k=94 (about 6% redundancy) in 3.9
seconds, where the "par2" tool encoded the file with about 6% redundancy in
27 seconds.  zfec encoded the same file with m=12, k=6 (100% redundancy) in
4.1 seconds, where par2 encoded it with about 100% redundancy in 7 minutes
and 56 seconds.

The underlying C library in benchmark mode encoded from a file at about 
4.9 million bytes per second and decoded at about 5.8 million bytes per second.

On Peter's fancy Intel Mac laptop (2.16 GHz Core Duo), it encoded from a file
at about 6.2 million bytes per second.

On my even fancier Intel Mac laptop (2.33 GHz Core Duo), it encoded from a file
at about 6.8 million bytes per second.

On my old PowerPC G4 867 MHz Mac laptop, it encoded from a file at about 1.3
million bytes per second.


 * API

Each block is associated with "blocknum".  The blocknum of each primary block is
its index (starting from zero), so the 0'th block is the first primary block,
which is the first few bytes of the file, the 1'st block is the next primary
block, which is the next few bytes of the file, and so on.  The last primary
block has blocknum k-1.  The blocknum of each secondary block is an arbitrary
integer between k and 255 inclusive.  (When using the Python API, if you don't
specify which blocknums you want for your secondary blocks when invoking
encode(), then it will by default provide the blocks with ids from k to m-1
inclusive.)

 ** C API

fec_encode() takes as input an array of k pointers, where each pointer points
to a memory buffer containing the input data (i.e., the i'th buffer contains
the i'th primary block).  There is also a second parameter which is an array of
the blocknums of the secondary blocks which are to be produced.  (Each element
in that array is required to be the blocknum of a secondary block, i.e. it is
required to be >= k and < m.)

The output from fec_encode() is the requested set of secondary blocks which are
written into output buffers provided by the caller.

fec_decode() takes as input an array of k pointers, where each pointer points
to a buffer containing a block.  There is also a separate input parameter which
is an array of blocknums, indicating the blocknum of each of the blocks which is
being passed in.

The output from fec_decode() is the set of primary blocks which were missing
from the input and had to be reconstructed.  These reconstructed blocks are
written into putput buffers provided by the caller.

 ** Python API

encode() and decode() take as input a sequence of k buffers, where a "sequence"
is any object that implements the Python sequence protocol (such as a list or
tuple) and a "buffer" is any object that implements the Python buffer protocol
(such as a string or array).  The contents that are required to be present in
these buffers are the same as for the C API.

encode() also takes a list of desired blocknums.  Unlike the C API, the Python
API accepts blocknums of primary blocks as well as secondary blocks in its list
of desired blocknums.  encode() returns a list of buffer objects which contain
the blocks requested.  For each requested block which is a primary block, the
resulting list contains a reference to the apppropriate primary block from the
input list.  For each requested block which is a secondary block, the list
contains a newly created string object containing that block.

decode() also takes a list of integers indicating the blocknums of the blocks
being passed int.  decode() returns a list of buffer objects which contain all
of the primary blocks of the original data (in order).  For each primary block
which was present in the input list, then the result list simply contains a
reference to the object that was passed in the input list.  For each primary
block which was not present in the input, the result list contains a newly
created string object containing that primary block.

Beware of a "gotcha" that can result from the combination of mutable data and
the fact that the Python API returns references to inputs when possible.

Returning references to its inputs is efficient since it avoids making an
unnecessary copy of the data, but if the object which was passed as input is
mutable and if that object is mutated after the call to zfec returns, then the
result from zfec -- which is just a reference to that same object -- will also
be mutated.  This subtlety is the price you pay for avoiding data copying.  If
you don't want to have to worry about this then you can simply use immutable
objects (e.g. Python strings) to hold the data that you pass to zfec.


 * Utilities

The filefec.py module has a utility function for efficiently reading a file
and encoding it piece by piece.  This module is used by the "zfec" and 
"zunfec" command-line tools from the bin/ directory.


 * Dependencies

A C compiler is required.  To use the Python API or the command-line tools a
Python interpreter is also required.  We have tested it with Python v2.4 and
v2.5.


 * Acknowledgements

Thanks to the author of the original fec lib, Luigi Rizzo, and the folks that
contributed to it: Phil Karn, Robert Morelos-Zaragoza, Hari Thirumoorthy, and
Dan Rubenstein.  Thanks to the Mnet hackers who wrote an earlier Python
wrapper, especially Myers Carpenter and Hauke Johannknecht.  Thanks to Brian
Warner and Amber O'Whielacronx for help with the API, documentation, 
debugging, compression, and unit tests.  Thanks to the creators of GCC 
(starting with Richard M. Stallman) and Valgrind (starting with Julian Seward) 
for a pair of excellent tools.  Thanks to my coworkers at Allmydata -- 
http://allmydata.com -- Fabrice Grinda, Peter Secor, Rob Kinninmont, Brian 
Warner, Zandr Milewski, Justin Boreta, Mark Meras for sponsoring this work and 
releasing it under a Free Software licence.


Enjoy!

Zooko Wilcox-O'Hearn
2007-04-27
Boulder, Colorado
