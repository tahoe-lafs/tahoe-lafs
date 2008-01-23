
# nevow requires all these for its voodoo module import time adaptor registrations
from nevow import accessors, appserver, static, rend, url, util, query, i18n, flat
from nevow import guard, stan, testutil, context
from nevow.flat import flatmdom, flatstan, twist
from formless import webform, processors, annotate, iformless
from decimal import Decimal


#if sys.platform in ['darwin', ]:
from nevow.flat import flatsax
from xml.parsers import expat
from xml.sax import expatreader, sax2exts
from xml.sax.drivers2 import drv_pyexpat, drv_xmlproc
junk = [ flatsax, expat, expatreader, sax2exts, drv_pyexpat, drv_xmlproc, ]



import allmydata.web

# junk to appease pyflakes's outrage at py2exe's needs
junk = [
    accessors, appserver, static, rend, url, util, query, i18n, flat, guard, stan, testutil,
    context, flatmdom, flatstan, twist, webform, processors, annotate, iformless, Decimal,
    allmydata,
    ]

