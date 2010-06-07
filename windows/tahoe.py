from allmydata.util import pkgresutil # override the pkg_resources zip provider for py2exe deployment
pkgresutil.install() # this is done before nevow is imported by depends
import depends # import dependencies so that py2exe finds them
_junk = depends # appease pyflakes

import sys
from ctypes import WINFUNCTYPE, POINTER, byref, c_wchar_p, c_int, windll
from allmydata.scripts import runner

GetCommandLineW = WINFUNCTYPE(c_wchar_p)(("GetCommandLineW", windll.kernel32))
CommandLineToArgvW = WINFUNCTYPE(POINTER(c_wchar_p), c_wchar_p, POINTER(c_int)) \
                         (("CommandLineToArgvW", windll.shell32))

argc = c_int(0)
argv = CommandLineToArgvW(GetCommandLineW(), byref(argc))
argv_utf8 = [argv[i].encode('utf-8') for i in xrange(1, argc.value)]

rc = runner(argv_utf8, install_node_control=False)
sys.exit(rc)