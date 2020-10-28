
# This checks that we can import the right versions of all dependencies.
# Import this first to suppress deprecation warnings.
import allmydata

from decimal import Decimal
from xml.dom import minidom

import allmydata.web

# We import these things to give PyInstaller's dependency resolver some hints
# about what it needs to include.  We don't use them otherwise _here_ but
# other parts of the codebase do.  pyflakes points out that they are unused
# unless we use them.  So ... use them.
Decimal
minidom
allmydata

from allmydata.scripts import runner

runner.run()
