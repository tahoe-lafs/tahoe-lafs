
# nevow requires all these for its voodoo module import time adaptor registrations
from nevow import accessors, appserver, static, rend, url, util, query, i18n, flat
from nevow import guard, stan, testutil, context
from nevow.flat import flatmdom, flatstan, twist
from formless import webform, processors, annotate, iformless
from decimal import Decimal

# junk to appease pyflakes's outrage at py2exe's needs
junk = [
    accessors, appserver, static, rend, url, util, query, i18n, flat, guard, stan, testutil,
    context, flatmdom, flatstan, twist, webform, processors, annotate, iformless, Decimal,
    ]
