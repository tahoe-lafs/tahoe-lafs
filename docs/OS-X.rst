==============
OS-X Packaging
==============

Pre-built Tahoe-LAFS ".pkg" installers for OS-X are generated with each
source-code commit. These installers offer an easy way to get Tahoe and all
its dependencies installed on your Mac. They do not yet provide a
double-clickable application: after installation, you will have a "tahoe"
command-line tool, which you can use from a shell (a Terminal window) just as
if you'd installed from source.

Installers are available from this directory:

 https://tahoe-lafs.org/source/tahoe-lafs/tarballs/OS-X-packages/

Download the latest .pkg file to your computer and double-click on it. This
will install to /Applications/tahoe.app, however the app icon there is not
how you use Tahoe (launching it will get you a dialog box with a reminder to
use Terminal). `/Applications/tahoe.app/bin/tahoe` is the executable. You can
symlink this to /usr/local/bin/ or ~/bin/ to make it more convenient to run.

Tahoe-LAFS can also be installed on OS-X with pip, as described in the
README.
