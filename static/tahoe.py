
# This checks that we can import the right versions of all dependencies.
# Import this first to suppress deprecation warnings.
import allmydata

# nevow requires all these for its voodoo module import time adaptor registrations
from nevow import accessors, appserver, static, rend, url, util, query, i18n, flat
from nevow import guard, stan, testutil, context
from nevow.flat import flatmdom, flatstan, twist
from formless import webform, processors, annotate, iformless
from decimal import Decimal
from xml.dom import minidom

import allmydata.web

import mock

# junk to appease pyflakes's outrage
[
    accessors, appserver, static, rend, url, util, query, i18n, flat, guard, stan, testutil,
    context, flatmdom, flatstan, twist, webform, processors, annotate, iformless, Decimal,
    minidom, allmydata, mock,
]

from allmydata.scripts import runner

runner.run()