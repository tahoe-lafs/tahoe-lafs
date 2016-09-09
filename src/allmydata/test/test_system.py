
import os, re, sys, time, simplejson

from twisted.trial import unittest
from twisted.internet import defer
from twisted.application import service

import allmydata
from allmydata import client, uri
from allmydata.introducer.server import IntroducerNode
from allmydata.storage.mutable import MutableShareFile
from allmydata.storage.server import si_a2b
from allmydata.immutable import offloaded, upload
from allmydata.immutable.literal import LiteralFileNode
from allmydata.immutable.filenode import ImmutableFileNode
from allmydata.util import idlib, mathutil, pollmixin, fileutil, iputil
from allmydata.util import log, base32
from allmydata.util.encodingutil import quote_output, unicode_to_argv
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.util.consumer import MemoryConsumer, download_to_data
from allmydata.stats import StatsGathererService
from allmydata.interfaces import IDirectoryNode, IFileNode, \
     NoSuchChildError, NoSharesError
from allmydata.monitor import Monitor
from allmydata.mutable.common import NotWriteableError
from allmydata.mutable import layout as mutable_layout
from allmydata.mutable.publish import MutableData

from foolscap.api import DeadReferenceError, fireEventually, flushEventualQueue
from twisted.python.failure import Failure
from twisted.web.client import getPage
from twisted.web.error import Error

from .common import TEST_RSA_KEY_SIZE

# TODO: move this to common or common_util
from allmydata.test.test_runner import RunBinTahoeMixin
from . import common_util as testutil
from .common_util import run_cli

LARGE_DATA = """
This is some data to publish to the remote grid.., which needs to be large
enough to not fit inside a LIT uri.
"""

# our system test uses the same Tub certificates each time, to avoid the
# overhead of key generation
SYSTEM_TEST_CERTS = [
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQyMDVaFw0wOTA3MjUyMjQyMDVaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAxHCWajrR
2h/iurw8k93m8WUdE3xypJiiAITw7GkKlKbCLD+dEce2MXwVVYca0n/MZZsj89Cu
Ko0lLjksMseoSDoj98iEmVpaY5mc2ntpQ+FXdoEmPP234XRWEg2HQ+EaK6+WkGQg
DDXQvFJCVCQk/n1MdAwZZ6vqf2ITzSuD44kCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQBn6qPKGdFjWJy7sOOTUFfm/THhHQqAh1pBDLkjR+OtzuobCoP8n8J1LNG3Yxds
Jj7NWQL7X5TfOlfoi7e9jK0ujGgWh3yYU6PnHzJLkDiDT3LCSywQuGXCjh0tOStS
2gaCmmAK2cfxSStKzNcewl2Zs8wHMygq8TLFoZ6ozN1+xQ==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQDEcJZqOtHaH+K6vDyT3ebxZR0TfHKkmKIAhPDsaQqUpsIsP50R
x7YxfBVVhxrSf8xlmyPz0K4qjSUuOSwyx6hIOiP3yISZWlpjmZzae2lD4Vd2gSY8
/bfhdFYSDYdD4Rorr5aQZCAMNdC8UkJUJCT+fUx0DBlnq+p/YhPNK4PjiQIDAQAB
AoGAZyDMdrymiyMOPwavrtlicvyohSBid3MCKc+hRBvpSB0790r2RO1aAySndp1V
QYmCXx1RhKDbrs8m49t0Dryu5T+sQrFl0E3usAP3vvXWeh4jwJ9GyiRWy4xOEuEQ
3ewjbEItHqA/bRJF0TNtbOmZTDC7v9FRPf2bTAyFfTZep5kCQQD33q1RA8WUYtmQ
IArgHqt69i421lpXlOgqotFHwTx4FiGgVzDQCDuXU6txB9EeKRM340poissav/n6
bkLZ7/VDAkEAyuIPkeI59sE5NnmW+N47NbCfdM1Smy1YxZpv942EmP9Veub5N0dw
iK5bLAgEguUIjpTsh3BRmsE9Xd+ItmnRQwJBAMZhbg19G1EbnE0BmDKv2UbcaThy
bnPSNc6J6T2opqDl9ZvCrMqTDD6dNIWOYAvni/4a556sFsoeBBAu10peBskCQE6S
cB86cuJagLLVMh/dySaI6ahNoFFSpY+ZuQUxfInYUR2Q+DFtbGqyw8JwtHaRBthZ
WqU1XZVGg2KooISsxIsCQQD1PS7//xHLumBb0jnpL7n6W8gmiTyzblT+0otaCisP
fN6rTlwV1o8VsOUAz0rmKO5RArCbkmb01WtMgPCDBYkk
-----END RSA PRIVATE KEY-----
""", # 0
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQyMDVaFw0wOTA3MjUyMjQyMDVaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAs9CALdmW
kJ6r0KPSLdGCA8rzQKxWayrMckT22ZtbRv3aw6VA96dWclpY+T2maV0LrAzmMSL8
n61ydJHM33iYDOyWbwHWN45XCjY/e20PL54XUl/DmbBHEhQVQLIfCldcRcnWEfoO
iOhDJfWpDO1dmP/aOYLdkZCZvBtPAfyUqRcCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQAN9eaCREkzzk4yPIaWYkWHg3Igs1vnOR/iDw3OjyxO/xJFP2lkA2WtrwL2RTRq
dxA8gwdPyrWgdiZElwZH8mzTJ4OdUXLSMclLOg9kvH6gtSvhLztfEDwDP1wRhikh
OeWWu2GIC+uqFCI1ftoGgU+aIa6yrHswf66rrQvBSSvJPQ==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQCz0IAt2ZaQnqvQo9It0YIDyvNArFZrKsxyRPbZm1tG/drDpUD3
p1ZyWlj5PaZpXQusDOYxIvyfrXJ0kczfeJgM7JZvAdY3jlcKNj97bQ8vnhdSX8OZ
sEcSFBVAsh8KV1xFydYR+g6I6EMl9akM7V2Y/9o5gt2RkJm8G08B/JSpFwIDAQAB
AoGBAIUy5zCPpSP+FeJY6CG+t6Pdm/IFd4KtUoM3KPCrT6M3+uzApm6Ny9Crsor2
qyYTocjSSVaOxzn1fvpw4qWLrH1veUf8ozMs8Z0VuPHD1GYUGjOXaBPXb5o1fQL9
h7pS5/HrDDPN6wwDNTsxRf/fP58CnfwQUhwdoxcx8TnVmDQxAkEA6N3jBXt/Lh0z
UbXHhv3QBOcqLZA2I4tY7wQzvUvKvVmCJoW1tfhBdYQWeQv0jzjL5PzrrNY8hC4l
8+sFM3h5TwJBAMWtbFIEZfRSG1JhHK3evYHDTZnr/j+CdoWuhzP5RkjkIKsiLEH7
2ZhA7CdFQLZF14oXy+g1uVCzzfB2WELtUbkCQQDKrb1XWzrBlzbAipfkXWs9qTmj
uJ32Z+V6+0xRGPOXxJ0sDDqw7CeFMfchWg98zLFiV+SEZV78qPHtkAPR3ayvAkB+
hUMhM4N13t9x2IoclsXAOhp++9bdG0l0woHyuAdOPATUw6iECwf4NQVxFRgYEZek
4Ro3Y7taddrHn1dabr6xAkAic47OoLOROYLpljmJJO0eRe3Z5IFe+0D2LfhAW3LQ
JU+oGq5pCjfnoaDElRRZn0+GmunnWeQEYKoflTi/lI9d
-----END RSA PRIVATE KEY-----
""", # 1
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQyMDZaFw0wOTA3MjUyMjQyMDZaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAsxG7LTrz
DF+9wegOR/BRJhjSumPUbYQnNAUKtPraFsGjAJILP44AHdnHt1MONLgTeX1ynapo
q6O/q5cdKtBB7uEh7FpkLCCwpZt/m0y79cynn8AmWoQVgl8oS0567UmPeJnTzFPv
dmT5dlaQALeX5YGceAsEvhmAsdOMttaor38CAwEAATANBgkqhkiG9w0BAQQFAAOB
gQA345rxotfvh2kfgrmRzAyGewVBV4r23Go30GSZir8X2GoH3qKNwO4SekAohuSw
AiXzLUbwIdSRSqaLFxSC7Duqc9eIeFDAWjeEmpfFLBNiw3K8SLA00QrHCUXnECTD
b/Kk6OGuvPOiuuONVjEuEcRdCH3/Li30D0AhJaMynjhQJQ==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQCzEbstOvMMX73B6A5H8FEmGNK6Y9RthCc0BQq0+toWwaMAkgs/
jgAd2ce3Uw40uBN5fXKdqmiro7+rlx0q0EHu4SHsWmQsILClm3+bTLv1zKefwCZa
hBWCXyhLTnrtSY94mdPMU+92ZPl2VpAAt5flgZx4CwS+GYCx04y21qivfwIDAQAB
AoGBAIlhFg/aRPL+VM9539LzHN60dp8GzceDdqwjHhbAySZiQlLCuJx2rcI4/U65
CpIJku9G/fLV9N2RkA/trDPXeGyqCTJfnNzyZcvvMscRMFqSGyc21Y0a+GS8bIxt
1R2B18epSVMsWSWWMypeEgsfv29LV7oSWG8UKaqQ9+0h63DhAkEA4i2L/rori/Fb
wpIBfA+xbXL/GmWR7xPW+3nG3LdLQpVzxz4rIsmtO9hIXzvYpcufQbwgVACyMmRf
TMABeSDM7wJBAMquEdTaVXjGfH0EJ7z95Ys2rYTiCXjBfyEOi6RXXReqV9SXNKlN
aKsO22zYecpkAjY1EdUdXWP/mNVEybjpZnECQQCcuh0JPS5RwcTo9c2rjyBOjGIz
g3B1b5UIG2FurmCrWe6pgO3ZJFEzZ/L2cvz0Hj5UCa2JKBZTDvRutZoPumfnAkAb
nSW+y1Rz1Q8m9Ub4v9rjYbq4bRd/RVWtyk6KQIDldYbr5wH8wxgsniSVKtVFFuUa
P5bDY3HS6wMGo42cTOhxAkAcdweQSQ3j7mfc5vh71HeAC1v/VAKGehGOUdeEIQNl
Sb2WuzpZkbfsrVzW6MdlgY6eE7ufRswhDPLWPC8MP0d1
-----END RSA PRIVATE KEY-----
""", # 2
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQyMDZaFw0wOTA3MjUyMjQyMDZaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAxnH+pbOS
qlJlsHpKUQtV0oN1Mv+ESG+yUDxStFFGjkJv/UIRzpxqFqY/6nJ3D03kZsDdcXyi
CfV9hPYQaVNMn6z+puPmIagfBQ0aOyuI+nUhCttZIYD9071BjW5bCMX5NZWL/CZm
E0HdAZ77H6UrRckJ7VR8wAFpihBxD5WliZcCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQAwXqY1Sjvp9JSTHKklu7s0T6YmH/BKSXrHpS2xO69svK+ze5/+5td3jPn4Qe50
xwRNZSFmSLuJLfCO32QJSJTB7Vs5D3dNTZ2i8umsaodm97t8hit7L75nXRGHKH//
xDVWAFB9sSgCQyPMRkL4wB4YSfRhoSKVwMvaz+XRZDUU0A==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXAIBAAKBgQDGcf6ls5KqUmWwekpRC1XSg3Uy/4RIb7JQPFK0UUaOQm/9QhHO
nGoWpj/qcncPTeRmwN1xfKIJ9X2E9hBpU0yfrP6m4+YhqB8FDRo7K4j6dSEK21kh
gP3TvUGNblsIxfk1lYv8JmYTQd0BnvsfpStFyQntVHzAAWmKEHEPlaWJlwIDAQAB
AoGAdHNMlXwtItm7ZrY8ihZ2xFP0IHsk60TwhHkBp2LSXoTKJvnwbSgIcUYZ18BX
8Zkp4MpoqEIU7HcssyuaMdR572huV2w0D/2gYJQLQ5JapaR3hMox3YG4wjXasN1U
1iZt7JkhKlOy+ElL5T9mKTE1jDsX2RAv4WALzMpYFo7vs4ECQQDxqrPaqRQ5uYS/
ejmIk05nM3Q1zmoLtMDrfRqrjBhaf/W3hqGihiqN2kL3PIIYcxSRWiyNlYXjElsR
2sllBTe3AkEA0jcMHVThwKt1+Ce5VcE7N6hFfbsgISTjfJ+Q3K2NkvJkmtE8ZRX5
XprssnPN8owkfF5yuKbcSZL3uvaaSGN9IQJAfTVnN9wwOXQwHhDSbDt9/KRBCnum
n+gHqDrKLaVJHOJ9SZf8eLswoww5c+UqtkYxmtlwie61Tp+9BXQosilQ4wJBAIZ1
XVNZmriBM4jR59L5MOZtxF0ilu98R+HLsn3kqLyIPF9mXCoQPxwLHkEan213xFKk
mt6PJDIPRlOZLqAEuuECQFQMCrn0VUwPg8E40pxMwgMETvVflPs/oZK1Iu+b7+WY
vBptAyhMu31fHQFnJpiUOyHqSZnOZyEn1Qu2lszNvUg=
-----END RSA PRIVATE KEY-----
""", # 3
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQyMDZaFw0wOTA3MjUyMjQyMDZaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAnjiOwipn
jigDuNMfNG/tBJhPwYUHhSbQdvrTubhsxw1oOq5XpNqUwRtC8hktOKM3hghyqExP
62EOi0aJBkRhtwtPSLBCINptArZLfkog/nTIqVv4eLEzJ19nTi/llHHWKcgA6XTI
sU/snUhGlySA3RpETvXqIJTauQRZz0kToSUCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQCQ+u/CsX5WC5m0cLrpyIS6qZa62lrB3mj9H1aIQhisT5kRsMz3FJ1aOaS8zPRz
w0jhyRmamCcSsWf5WK539iOtsXbKMdAyjNtkQO3g+fnsLgmznAjjst24jfr+XU59
0amiy1U6TY93gtEBZHtiLldPdUMsTuFbBlqbcMBQ50x9rA==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXAIBAAKBgQCeOI7CKmeOKAO40x80b+0EmE/BhQeFJtB2+tO5uGzHDWg6rlek
2pTBG0LyGS04ozeGCHKoTE/rYQ6LRokGRGG3C09IsEIg2m0Ctkt+SiD+dMipW/h4
sTMnX2dOL+WUcdYpyADpdMixT+ydSEaXJIDdGkRO9eoglNq5BFnPSROhJQIDAQAB
AoGAAPrst3s3xQOucjismtCOsVaYN+SxFTwWUoZfRWlFEz6cBLELzfOktEWM9p79
TrqEH4px22UNobGqO2amdql5yXwEFVhYQkRB8uDA8uVaqpL8NLWTGPRXxZ2DSU+n
7/FLf/TWT3ti/ZtXaPVRj6E2/Mq9AVEVOjUYzkNjM02OxcECQQDKEqmPbdZq2URU
7RbUxkq5aTp8nzAgbpUsgBGQ9PDAymhj60BDEP0q28Ssa7tU70pRnQ3AZs9txgmL
kK2g97FNAkEAyHH9cIb6qXOAJPIr/xamFGr5uuYw9TJPz/hfVkVimW/aZnBB+e6Q
oALJBDKJWeYPzdNbouJYg8MeU0qWdZ5DOQJADUk+1sxc/bd9U6wnBSRog1pU2x7I
VkmPC1b8ULCaJ8LnLDKqjf5O9wNuIfwPXB1DoKwX3F+mIcyUkhWYJO5EPQJAUj5D
KMqZSrGzYHVlC/M1Daee88rDR7fu+3wDUhiCDkbQq7tftrbl7GF4LRq3NIWq8l7I
eJq6isWiSbaO6Y+YMQJBAJFBpVhlY5Px2BX5+Hsfq6dSP3sVVc0eHkdsoZFFxq37
fksL/q2vlPczvBihgcxt+UzW/UrNkelOuX3i57PDvFs=
-----END RSA PRIVATE KEY-----
""", # 4
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQyMDZaFw0wOTA3MjUyMjQyMDZaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAsCQuudDF
zgmY5tDpT0TkUo8fpJ5JcvgCkLFpSDD8REpXhLFkHWhTmTj3CAxfv4lA3sQzHZxe
4S9YCb5c/VTbFEdgwc/wlxMmJiz2jYghdmWPBb8pBEk31YihIhC+u4kex6gJBH5y
ixiZ3PPRRMaOBBo+ZfM50XIyWbFOOM/7FwcCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQB4cFURaiiUx6n8eS4j4Vxrii5PtsaNEI4acANFSYknGd0xTP4vnmoivNmo5fWE
Q4hYtGezNu4a9MnNhcQmI20KzXmvhLJtkwWCgGOVJtMem8hDWXSALV1Ih8hmVkGS
CI1elfr9eyguunGp9eMMQfKhWH52WHFA0NYa0Kpv5BY33A==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICWwIBAAKBgQCwJC650MXOCZjm0OlPRORSjx+knkly+AKQsWlIMPxESleEsWQd
aFOZOPcIDF+/iUDexDMdnF7hL1gJvlz9VNsUR2DBz/CXEyYmLPaNiCF2ZY8FvykE
STfViKEiEL67iR7HqAkEfnKLGJnc89FExo4EGj5l8znRcjJZsU44z/sXBwIDAQAB
AoGABA7xXKqoxBSIh1js5zypHhXaHsre2l1Igdj0mgs25MPpvE7yBZNvyan8Vx0h
36Hj8r4Gh3og3YNfvem67sNTwNwONY0ep+Xho/3vG0jFATGduSXdcT04DusgZNqg
UJqW75cqxrD6o/nya5wUoN9NL5pcd5AgVMdOYvJGbrwQuaECQQDiCs/5dsUkUkeC
Tlur1wh0wJpW4Y2ctO3ncRdnAoAA9y8dELHXMqwKE4HtlyzHY7Bxds/BDh373EVK
rsdl+v9JAkEAx3xTmsOQvWa1tf/O30sdItVpGogKDvYqkLCNthUzPaL85BWB03E2
xunHcVVlqAOE5tFuw0/UEyEkOaGlNTJTzwJAPIVel9FoCUiKYuYt/z1swy3KZRaw
/tMmm4AZHvh5Y0jLcYHFy/OCQpRkhkOitqQHWunPyEXKW2PnnY5cTv68GQJAHG7H
B88KCUTjb25nkQIGxBlA4swzCtDhXkAb4rEA3a8mdmfuWjHPyeg2ShwO4jSmM7P0
Iph1NMjLff9hKcTjlwJARpItOFkYEdtSODC7FMm7KRKQnNB27gFAizsOYWD4D2b7
w1FTEZ/kSA9wSNhyNGt7dgUo6zFhm2u973HBCUb3dg==
-----END RSA PRIVATE KEY-----
""", # 5
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQ3NThaFw0wOTA3MjUyMjQ3NThaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAvhTRj1dA
NOfse/UBeTfMekZKxZHsNPr+qBYaveWAHDded/BMyMgaMV2n6HQdiDaRjJkzjHCF
3xBtpIJeEGUqfrF0ob8BIZXy3qk68eX/0CVUbgmjSBN44ahlo63NshyXmZtEAkRV
VE/+cRKw3N2wtuTed5xwfNcL6dg4KTOEYEkCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQCN+CLuVwLeWjSdVbdizYyrOVckqtwiIHG9BbGMlcIdm0qpvD7V7/sN2csk5LaT
BNiHi1t5628/4UHqqodYmFw8ri8ItFwB+MmTJi11CX6dIP9OUhS0qO8Z/BKtot7H
j04oNwl+WqZZfHIYwTIEL0HBn60nOvCQPDtnWG2BhpUxMA==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQC+FNGPV0A05+x79QF5N8x6RkrFkew0+v6oFhq95YAcN1538EzI
yBoxXafodB2INpGMmTOMcIXfEG2kgl4QZSp+sXShvwEhlfLeqTrx5f/QJVRuCaNI
E3jhqGWjrc2yHJeZm0QCRFVUT/5xErDc3bC25N53nHB81wvp2DgpM4RgSQIDAQAB
AoGALl2BqIdN4Bnac3oV++2CcSkIQB0SEvJOf820hDGhCEDxSCxTbn5w9S21MVxx
f7Jf2n3cNxuTbA/jzscGDtW+gXCs+WAbAr5aOqHLUPGEobhKQrQT2hrxQHyv3UFp
0tIl9eXFknOyVAaUJ3athK5tyjSiCZQQHLGzeLaDSKVAPqECQQD1GK7DkTcLaSvw
hoTJ3dBK3JoKT2HHLitfEE0QV58mkqFMjofpe+nyeKWvEb/oB4WBp/cfTvtf7DJK
zl1OSf11AkEAxomWmJeub0xpqksCmnVI1Jt1mvmcE4xpIcXq8sxzLHRc2QOv0kTw
IcFl4QcN6EQBmE+8kl7Tx8SPAVKfJMoZBQJAGsUFYYrczjxAdlba7glyFJsfn/yn
m0+poQpwwFYxpc7iGzB+G7xTAw62WfbAVSFtLYog7aR8xC9SFuWPP1vJeQJBAILo
xBj3ovgWTXIRJbVM8mnl28UFI0msgsHXK9VOw/6i93nMuYkPFbtcN14KdbwZ42dX
5EIrLr+BNr4riW4LqDUCQQCbsEEpTmj3upKUOONPt+6CH/OOMjazUzYHZ/3ORHGp
Q3Wt+I4IrR/OsiACSIQAhS4kBfk/LGggnj56DrWt+oBl
-----END RSA PRIVATE KEY-----
""", #6
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQ3NThaFw0wOTA3MjUyMjQ3NThaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAtKhx6sEA
jn6HWc6T2klwlPn0quyHtATIw8V3ezP46v6g2rRS7dTywo4GTP4vX58l+sC9z9Je
qhQ1rWSwMK4FmnDMZCu7AVO7oMIXpXdSz7l0bgCnNjvbpkA2pOfbB1Z8oj8iebff
J33ID5DdkmCzqYVtKpII1o/5z7Jo292JYy8CAwEAATANBgkqhkiG9w0BAQQFAAOB
gQA0PYMA07wo9kEH4fv9TCfo+zz42Px6lUxrQBPxBvDiGYhk2kME/wX0IcoZPKTV
WyBGmDAYWvFaHWbrbbTOfzlLWfYrDD913hCi9cO8iF8oBqRjIlkKcxAoe7vVg5Az
ydVcrY+zqULJovWwyNmH1QNIQfMat0rj7fylwjiS1y/YsA==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXAIBAAKBgQC0qHHqwQCOfodZzpPaSXCU+fSq7Ie0BMjDxXd7M/jq/qDatFLt
1PLCjgZM/i9fnyX6wL3P0l6qFDWtZLAwrgWacMxkK7sBU7ugwheld1LPuXRuAKc2
O9umQDak59sHVnyiPyJ5t98nfcgPkN2SYLOphW0qkgjWj/nPsmjb3YljLwIDAQAB
AoGAU4CYRv22mCZ7wVLunDLdyr5ODMMPZnHfqj2XoGbBYz0WdIBs5GlNXAfxeZzz
oKsbDvAPzANcphh5RxAHMDj/dT8rZOez+eJrs1GEV+crl1T9p83iUkAuOJFtgUgf
TtQBL9vHaj7DfvCEXcBPmN/teDFmAAOyUNbtuhTkRa3PbuECQQDwaqZ45Kr0natH
V312dqlf9ms8I6e873pAu+RvA3BAWczk65eGcRjEBxVpTvNEcYKFrV8O5ZYtolrr
VJl97AfdAkEAwF4w4KJ32fLPVoPnrYlgLw86NejMpAkixblm8cn51avPQmwbtahb
BZUuca22IpgDpjeEk5SpEMixKe/UjzxMewJBALy4q2cY8U3F+u6sshLtAPYQZIs3
3fNE9W2dUKsIQvRwyZMlkLN7UhqHCPq6e+HNTM0MlCMIfAPkf4Rdy4N6ZY0CQCKE
BAMaQ6TwgzFDw5sIjiCDe+9WUPmRxhJyHL1/fvtOs4Z4fVRP290ZklbFU2vLmMQH
LBuKzfb7+4XJyXrV1+cCQBqfPFQQZLr5UgccABYQ2jnWVbJPISJ5h2b0cwXt+pz/
8ODEYLjqWr9K8dtbgwdpzwbkaGhQYpyvsguMvNPMohs=
-----END RSA PRIVATE KEY-----
""", #7
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQ3NThaFw0wOTA3MjUyMjQ3NThaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAnBfNHycn
5RnYzDN4EWTk2q1BBxA6ZYtlG1WPkj5iKeaYKzUk58zBL7mNOA0ucq+yTwh9C4IC
EutWPaKBSKY5XI+Rdebh+Efq+urtOLgfJHlfcCraEx7hYN+tqqMVgEgnO/MqIsn1
I1Fvnp89mSYbQ9tmvhSH4Hm+nbeK6iL2tIsCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQBt9zxfsKWoyyV764rRb6XThuTDMNSDaVofqePEWjudAbDu6tp0pHcrL0XpIrnT
3iPgD47pdlwQNbGJ7xXwZu2QTOq+Lv62E6PCL8FljDVoYqR3WwJFFUigNvBT2Zzu
Pxx7KUfOlm/M4XUSMu31sNJ0kQniBwpkW43YmHVNFb/R7g==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQCcF80fJyflGdjMM3gRZOTarUEHEDpli2UbVY+SPmIp5pgrNSTn
zMEvuY04DS5yr7JPCH0LggIS61Y9ooFIpjlcj5F15uH4R+r66u04uB8keV9wKtoT
HuFg362qoxWASCc78yoiyfUjUW+enz2ZJhtD22a+FIfgeb6dt4rqIva0iwIDAQAB
AoGBAIHstcnWd7iUeQYPWUNxLaRvTY8pjNH04yWLZEOgNWkXDVX5mExw++RTmB4t
qpm/cLWkJSEtB7jjthb7ao0j/t2ljqfr6kAbClDv3zByAEDhOu8xB/5ne6Ioo+k2
dygC+GcVcobhv8qRU+z0fpeXSP8yS1bQQHOaa17bSGsncvHRAkEAzwsn8jBTOqaW
6Iymvr7Aql++LiwEBrqMMRVyBZlkux4hiKa2P7XXEL6/mOPR0aI2LuCqE2COrO7R
0wAFZ54bjwJBAMEAe6cs0zI3p3STHwA3LoSZB81lzLhGUnYBvOq1yoDSlJCOYpld
YM1y3eC0vwiOnEu3GG1bhkW+h6Kx0I/qyUUCQBiH9NqwORxI4rZ4+8S76y4EnA7y
biOx9KxYIyNgslutTUHYpt1TmUDFqQPfclvJQWw6eExFc4Iv5bJ/XSSSyicCQGyY
5PrwEfYTsrm5fpwUcKxTnzxHp6WYjBWybKZ0m/lYhBfCxmAdVrbDh21Exqj99Zv0
7l26PhdIWfGFtCEGrzECQQCtPyXa3ostSceR7zEKxyn9QBCNXKARfNNTBja6+VRE
qDC6jLqzu/SoOYaqa13QzCsttO2iZk8Ygfy3Yz0n37GE
-----END RSA PRIVATE KEY-----
""", #8
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQ3NThaFw0wOTA3MjUyMjQ3NThaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEA4mnLf+x0
CWKDKP5PLZ87t2ReSDE/J5QoI5VhE0bXaahdhPrQTC2wvOpT+N9nzEpI9ASh/ejV
kYGlc03nNKRL7zyVM1UyGduEwsRssFMqfyJhI1p+VmxDMWNplex7mIAheAdskPj3
pwi2CP4VIMjOj368AXvXItPzeCfAhYhEVaMCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQAEzmwq5JFI5Z0dX20m9rq7NKgwRyAH3h5aE8bdjO8nEc69qscfDRx79Lws3kK8
A0LG0DhxKB8cTNu3u+jy81tjcC4pLNQ5IKap9ksmP7RtIHfTA55G8M3fPl2ZgDYQ
ZzsWAZvTNXd/eme0SgOzD10rfntA6ZIgJTWHx3E0RkdwKw==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQDiact/7HQJYoMo/k8tnzu3ZF5IMT8nlCgjlWETRtdpqF2E+tBM
LbC86lP432fMSkj0BKH96NWRgaVzTec0pEvvPJUzVTIZ24TCxGywUyp/ImEjWn5W
bEMxY2mV7HuYgCF4B2yQ+PenCLYI/hUgyM6PfrwBe9ci0/N4J8CFiERVowIDAQAB
AoGAQYTl+8XcKl8Un4dAOG6M5FwqIHAH25c3Klzu85obehrbvUCriG/sZi7VT/6u
VeLlS6APlJ+NNgczbrOLhaNJyYzjICSt8BI96PldFUzCEkVlgE+29pO7RNoZmDYB
dSGyIDrWdVYfdzpir6kC0KDcrpA16Sc+/bK6Q8ALLRpC7QECQQD7F7fhIQ03CKSk
lS4mgDuBQrB/52jXgBumtjp71ANNeaWR6+06KDPTLysM+olsh97Q7YOGORbrBnBg
Y2HPnOgjAkEA5taZaMfdFa8V1SPcX7mgCLykYIujqss0AmauZN/24oLdNE8HtTBF
OLaxE6PnQ0JWfx9KGIy3E0V3aFk5FWb0gQJBAO4KFEaXgOG1jfCBhNj3JHJseMso
5Nm4F366r0MJQYBHXNGzqphB2K/Svat2MKX1QSUspk2u/a0d05dtYCLki6UCQHWS
sChyQ+UbfF9HGKOZBC3vBzo1ZXNEdIUUj5bJjBHq3YgbCK38nAU66A482TmkvDGb
Wj4OzeB+7Ua0yyJfggECQQDVlAa8HqdAcrbEwI/YfPydFsavBJ0KtcIGK2owQ+dk
dhlDnpXDud/AtX4Ft2LaquQ15fteRrYjjwI9SFGytjtp
-----END RSA PRIVATE KEY-----
""", #9
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQ3NThaFw0wOTA3MjUyMjQ3NThaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAueLfowPT
kXXtHeU2FZSz2mJhHmjqeyI1oMoyyggonccx65vMxaRfljnz2dOjVVYpCOn/LrdP
wVxHO8KNDsmQeWPRjnnBa2dFqqOnp/8gEJFJBW7K/gI9se6o+xe9QIWBq6d/fKVR
BURJe5TycLogzZuxQn1xHHILa3XleYuHAbMCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQBEC1lfC3XK0galQC96B7faLpnQmhn5lX2FUUoFIQQtBTetoE+gTqnLSOIZcOK4
pkT3YvxUvgOV0LOLClryo2IknMMGWRSAcXtVUBBLRHVTSSuVUyyLr5kdRU7B4E+l
OU0j8Md/dzlkm//K1bzLyUaPq204ofH8su2IEX4b3IGmAQ==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICWwIBAAKBgQC54t+jA9ORde0d5TYVlLPaYmEeaOp7IjWgyjLKCCidxzHrm8zF
pF+WOfPZ06NVVikI6f8ut0/BXEc7wo0OyZB5Y9GOecFrZ0Wqo6en/yAQkUkFbsr+
Aj2x7qj7F71AhYGrp398pVEFREl7lPJwuiDNm7FCfXEccgtrdeV5i4cBswIDAQAB
AoGAO4PnJHNaLs16AMNdgKVevEIZZDolMQ1v7C4w+ryH/JRFaHE2q+UH8bpWV9zK
A82VT9RTrqpkb71S1VBiB2UDyz263XdAI/N2HcIVMmfKb72oV4gCI1KOv4DfFwZv
tVVcIdVEDBOZ2TgqK4opGOgWMDqgIAl2z3PbsIoNylZHEJECQQDtQeJFhEJGH4Qz
BGpdND0j2nnnJyhOFHJqikJNdul3uBwmxTK8FPEUUH/rtpyUan3VMOyDx3kX4OQg
GDNSb32rAkEAyJIZIJ0EMRHVedyWsfqR0zTGKRQ+qsc3sCfyUhFksWms9jsSS0DT
tVeTdC3F6EIAdpKOGhSyfBTU4jxwbFc0GQJADI4L9znEeAl66Wg2aLA2/Aq3oK/F
xjv2wgSG9apxOFCZzMNqp+FD0Jth6YtEReZMuldYbLDFi6nu6HPfY2Fa+QJAdpm1
lAxk6yMxiZK/5VRWoH6HYske2Vtd+aNVbePtF992ME/z3F3kEkpL3hom+dT1cyfs
MU3l0Ot8ip7Ul6vlGQJAegNzpcfl2GFSdWQMxQ+nN3woKnPqpR1M3jgnqvo7L4Xe
JW3vRxvfdrUuzdlvZ/Pbsu/vOd+cuIa4h0yD5q3N+g==
-----END RSA PRIVATE KEY-----
""", #10
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQ3NThaFw0wOTA3MjUyMjQ3NThaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAruBhwk+J
XdlwfKXXN8K+43JyEYCV7Fp7ZiES4t4AEJuQuBqJVMxpzeZzu2t/vVb59ThaxxtY
NGD3Xy6Og5dTv//ztWng8P7HwwvfbrUICU6zo6JAhg7kfaNa116krCYOkC/cdJWt
o5W+zsDmI1jUVGH0D73h29atc1gn6wLpAsMCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQAEJ/ITGJ9lK/rk0yHcenW8SHsaSTlZMuJ4yEiIgrJ2t71Rd6mtCC/ljx9USvvK
bF500whTiZlnWgKi02boBEKa44z/DytF6pljeNPefBQSqZyUByGEb/8Mn58Idyls
q4/d9iKXMPvbpQdcesOzgOffFZevLQSWyPRaIdYBOOiYUA==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQCu4GHCT4ld2XB8pdc3wr7jcnIRgJXsWntmIRLi3gAQm5C4GolU
zGnN5nO7a3+9Vvn1OFrHG1g0YPdfLo6Dl1O///O1aeDw/sfDC99utQgJTrOjokCG
DuR9o1rXXqSsJg6QL9x0la2jlb7OwOYjWNRUYfQPveHb1q1zWCfrAukCwwIDAQAB
AoGAcZAXC/dYrlBpIxkTRQu7qLqGZuVI9t7fabgqqpceFargdR4Odrn0L5jrKRer
MYrM8bjyAoC4a/NYUUBLnhrkcCQWO9q5fSQuFKFVWHY53SM63Qdqk8Y9Fmy/h/4c
UtwZ5BWkUWItvnTMgb9bFcvSiIhEcNQauypnMpgNknopu7kCQQDlSQT10LkX2IGT
bTUhPcManx92gucaKsPONKq2mP+1sIciThevRTZWZsxyIuoBBY43NcKKi8NlZCtj
hhSbtzYdAkEAw0B93CXfso8g2QIMj/HJJz/wNTLtg+rriXp6jh5HWe6lKWRVrce+
1w8Qz6OI/ZP6xuQ9HNeZxJ/W6rZPW6BGXwJAHcTuRPA1p/fvUvHh7Q/0zfcNAbkb
QlV9GL/TzmNtB+0EjpqvDo2g8XTlZIhN85YCEf8D5DMjSn3H+GMHN/SArQJBAJlW
MIGPjNoh5V4Hae4xqBOW9wIQeM880rUo5s5toQNTk4mqLk9Hquwh/MXUXGUora08
2XGpMC1midXSTwhaGmkCQQCdivptFEYl33PrVbxY9nzHynpp4Mi89vQF0cjCmaYY
N8L+bvLd4BU9g6hRS8b59lQ6GNjryx2bUnCVtLcey4Jd
-----END RSA PRIVATE KEY-----
""", #11
]

# To disable the pre-computed tub certs, uncomment this line.
#SYSTEM_TEST_CERTS = []

def flush_but_dont_ignore(res):
    d = flushEventualQueue()
    def _done(ignored):
        return res
    d.addCallback(_done)
    return d

class SystemTestMixin(pollmixin.PollMixin, testutil.StallMixin):

    # SystemTestMixin tests tend to be a lot of work, and we have a few
    # buildslaves that are pretty slow, and every once in a while these tests
    # run up against the default 120 second timeout. So increase the default
    # timeout. Individual test cases can override this, of course.
    timeout = 300

    def setUp(self):
        self.sparent = service.MultiService()
        self.sparent.startService()

        self.stats_gatherer = None
        self.stats_gatherer_furl = None

    def tearDown(self):
        log.msg("shutting down SystemTest services")
        d = self.sparent.stopService()
        d.addBoth(flush_but_dont_ignore)
        return d

    def getdir(self, subdir):
        return os.path.join(self.basedir, subdir)

    def add_service(self, s):
        s.setServiceParent(self.sparent)
        return s

    def set_up_nodes(self, NUMCLIENTS=5, use_stats_gatherer=False):
        self.numclients = NUMCLIENTS
        iv_dir = self.getdir("introducer")
        if not os.path.isdir(iv_dir):
            fileutil.make_dirs(iv_dir)
            fileutil.write(os.path.join(iv_dir, 'tahoe.cfg'),
                           "[node]\n" +
                           u"nickname = introducer \u263A\n".encode('utf-8') +
                           "web.port = tcp:0:interface=127.0.0.1\n")
            if SYSTEM_TEST_CERTS:
                os.mkdir(os.path.join(iv_dir, "private"))
                f = open(os.path.join(iv_dir, "private", "node.pem"), "w")
                f.write(SYSTEM_TEST_CERTS[0])
                f.close()
        iv = IntroducerNode(basedir=iv_dir)
        self.introducer = self.add_service(iv)
        self._get_introducer_web()
        d = defer.succeed(None)
        if use_stats_gatherer:
            d.addCallback(self._set_up_stats_gatherer)
        d.addCallback(self._set_up_nodes_2)
        if use_stats_gatherer:
            d.addCallback(self._grab_stats)
        return d

    def _get_introducer_web(self):
        f = open(os.path.join(self.getdir("introducer"), "node.url"), "r")
        self.introweb_url = f.read().strip()
        f.close()

    def _set_up_stats_gatherer(self, res):
        statsdir = self.getdir("stats_gatherer")
        fileutil.make_dirs(statsdir)
        portnum = iputil.allocate_tcp_port()
        location = "tcp:127.0.0.1:%d" % portnum
        fileutil.write(os.path.join(statsdir, "location"), location)
        port = "tcp:%d:interface=127.0.0.1" % portnum
        fileutil.write(os.path.join(statsdir, "port"), port)
        self.stats_gatherer_svc = StatsGathererService(statsdir)
        self.stats_gatherer = self.stats_gatherer_svc.stats_gatherer
        self.add_service(self.stats_gatherer_svc)

        d = fireEventually()
        sgf = os.path.join(statsdir, 'stats_gatherer.furl')
        def check_for_furl():
            return os.path.exists(sgf)
        d.addCallback(lambda junk: self.poll(check_for_furl, timeout=30))
        def get_furl(junk):
            self.stats_gatherer_furl = file(sgf, 'rb').read().strip()
        d.addCallback(get_furl)
        return d

    def _set_up_nodes_2(self, res):
        q = self.introducer
        self.introducer_furl = q.introducer_url
        self.clients = []
        basedirs = []
        for i in range(self.numclients):
            basedir = self.getdir("client%d" % i)
            basedirs.append(basedir)
            fileutil.make_dirs(os.path.join(basedir, "private"))
            if len(SYSTEM_TEST_CERTS) > (i+1):
                f = open(os.path.join(basedir, "private", "node.pem"), "w")
                f.write(SYSTEM_TEST_CERTS[i+1])
                f.close()

            config = "[client]\n"
            config += "introducer.furl = %s\n" % self.introducer_furl
            if self.stats_gatherer_furl:
                config += "stats_gatherer.furl = %s\n" % self.stats_gatherer_furl

            nodeconfig = "[node]\n"
            nodeconfig += (u"nickname = client %d \u263A\n" % (i,)).encode('utf-8')
            tub_port = iputil.allocate_tcp_port()
            # Don't let it use AUTO: there's no need for tests to use
            # anything other than 127.0.0.1
            nodeconfig += "tub.port = tcp:%d\n" % tub_port
            nodeconfig += "tub.location = tcp:127.0.0.1:%d\n" % tub_port

            if i == 0:
                # clients[0] runs a webserver and a helper
                config += nodeconfig
                config += "web.port = tcp:0:interface=127.0.0.1\n"
                config += "timeout.keepalive = 600\n"
                config += "[helper]\n"
                config += "enabled = True\n"
            elif i == 3:
                # clients[3] runs a webserver and uses a helper
                config += nodeconfig
                config += "web.port = tcp:0:interface=127.0.0.1\n"
                config += "timeout.disconnect = 1800\n"
            else:
                config += nodeconfig

            fileutil.write(os.path.join(basedir, 'tahoe.cfg'), config)

        # give subclasses a chance to append lines to the node's tahoe.cfg
        # files before they are launched.
        self._set_up_nodes_extra_config()

        # start clients[0], wait for it's tub to be ready (at which point it
        # will have registered the helper furl).
        c = self.add_service(client.Client(basedir=basedirs[0]))
        self.clients.append(c)
        c.set_default_mutable_keysize(TEST_RSA_KEY_SIZE)

        f = open(os.path.join(basedirs[0],"private","helper.furl"), "r")
        helper_furl = f.read()
        f.close()
        self.helper_furl = helper_furl
        if self.numclients >= 4:
            f = open(os.path.join(basedirs[3], 'tahoe.cfg'), 'ab+')
            f.write(
                  "[client]\n"
                  "helper.furl = %s\n" % helper_furl)
            f.close()

        # this starts the rest of the clients
        for i in range(1, self.numclients):
            c = self.add_service(client.Client(basedir=basedirs[i]))
            self.clients.append(c)
            c.set_default_mutable_keysize(TEST_RSA_KEY_SIZE)
        log.msg("STARTING")
        d = self.wait_for_connections()
        def _connected(res):
            log.msg("CONNECTED")
            # now find out where the web port was
            self.webish_url = self.clients[0].getServiceNamed("webish").getURL()
            if self.numclients >=4:
                # and the helper-using webport
                self.helper_webish_url = self.clients[3].getServiceNamed("webish").getURL()
        d.addCallback(_connected)
        return d

    def _set_up_nodes_extra_config(self):
        # for overriding by subclasses
        pass

    def _grab_stats(self, res):
        d = self.stats_gatherer.poll()
        return d

    def bounce_client(self, num):
        c = self.clients[num]
        d = c.disownServiceParent()
        # I think windows requires a moment to let the connection really stop
        # and the port number made available for re-use. TODO: examine the
        # behavior, see if this is really the problem, see if we can do
        # better than blindly waiting for a second.
        d.addCallback(self.stall, 1.0)
        def _stopped(res):
            new_c = client.Client(basedir=self.getdir("client%d" % num))
            self.clients[num] = new_c
            new_c.set_default_mutable_keysize(TEST_RSA_KEY_SIZE)
            self.add_service(new_c)
        d.addCallback(_stopped)
        d.addCallback(lambda res: self.wait_for_connections())
        def _maybe_get_webport(res):
            if num == 0:
                # now find out where the web port was
                self.webish_url = self.clients[0].getServiceNamed("webish").getURL()
        d.addCallback(_maybe_get_webport)
        return d

    def add_extra_node(self, client_num, helper_furl=None,
                       add_to_sparent=False):
        # usually this node is *not* parented to our self.sparent, so we can
        # shut it down separately from the rest, to exercise the
        # connection-lost code
        basedir = self.getdir("client%d" % client_num)
        if not os.path.isdir(basedir):
            fileutil.make_dirs(basedir)
        config = "[client]\n"
        config += "introducer.furl = %s\n" % self.introducer_furl
        if helper_furl:
            config += "helper.furl = %s\n" % helper_furl
        fileutil.write(os.path.join(basedir, 'tahoe.cfg'), config)

        c = client.Client(basedir=basedir)
        self.clients.append(c)
        c.set_default_mutable_keysize(TEST_RSA_KEY_SIZE)
        self.numclients += 1
        if add_to_sparent:
            c.setServiceParent(self.sparent)
        else:
            c.startService()
        d = self.wait_for_connections()
        d.addCallback(lambda res: c)
        return d

    def _check_connections(self):
        for c in self.clients:
            if not c.connected_to_introducer():
                return False
            sb = c.get_storage_broker()
            if len(sb.get_connected_servers()) != self.numclients:
                return False
            up = c.getServiceNamed("uploader")
            if up._helper_furl and not up._helper:
                return False
        return True

    def wait_for_connections(self, ignored=None):
        return self.poll(self._check_connections, timeout=200)

class CountingDataUploadable(upload.Data):
    bytes_read = 0
    interrupt_after = None
    interrupt_after_d = None

    def read(self, length):
        self.bytes_read += length
        if self.interrupt_after is not None:
            if self.bytes_read > self.interrupt_after:
                self.interrupt_after = None
                self.interrupt_after_d.callback(self)
        return upload.Data.read(self, length)

class SystemTest(SystemTestMixin, RunBinTahoeMixin, unittest.TestCase):
    timeout = 3600 # It takes longer than 960 seconds on Zandr's ARM box.

    def test_connections(self):
        self.basedir = "system/SystemTest/test_connections"
        d = self.set_up_nodes()
        self.extra_node = None
        d.addCallback(lambda res: self.add_extra_node(self.numclients))
        def _check(extra_node):
            self.extra_node = extra_node
            for c in self.clients:
                all_peerids = c.get_storage_broker().get_all_serverids()
                self.failUnlessEqual(len(all_peerids), self.numclients+1)
                sb = c.storage_broker
                permuted_peers = sb.get_servers_for_psi("a")
                self.failUnlessEqual(len(permuted_peers), self.numclients+1)

        d.addCallback(_check)
        def _shutdown_extra_node(res):
            if self.extra_node:
                return self.extra_node.stopService()
            return res
        d.addBoth(_shutdown_extra_node)
        return d
    # test_connections is subsumed by test_upload_and_download, and takes
    # quite a while to run on a slow machine (because of all the TLS
    # connections that must be established). If we ever rework the introducer
    # code to such an extent that we're not sure if it works anymore, we can
    # reinstate this test until it does.
    del test_connections

    def test_upload_and_download_random_key(self):
        self.basedir = "system/SystemTest/test_upload_and_download_random_key"
        return self._test_upload_and_download(convergence=None)

    def test_upload_and_download_convergent(self):
        self.basedir = "system/SystemTest/test_upload_and_download_convergent"
        return self._test_upload_and_download(convergence="some convergence string")

    def _test_upload_and_download(self, convergence):
        # we use 4000 bytes of data, which will result in about 400k written
        # to disk among all our simulated nodes
        DATA = "Some data to upload\n" * 200
        d = self.set_up_nodes()
        def _check_connections(res):
            for c in self.clients:
                c.encoding_params['happy'] = 5
                all_peerids = c.get_storage_broker().get_all_serverids()
                self.failUnlessEqual(len(all_peerids), self.numclients)
                sb = c.storage_broker
                permuted_peers = sb.get_servers_for_psi("a")
                self.failUnlessEqual(len(permuted_peers), self.numclients)
        d.addCallback(_check_connections)

        def _do_upload(res):
            log.msg("UPLOADING")
            u = self.clients[0].getServiceNamed("uploader")
            self.uploader = u
            # we crank the max segsize down to 1024b for the duration of this
            # test, so we can exercise multiple segments. It is important
            # that this is not a multiple of the segment size, so that the
            # tail segment is not the same length as the others. This actualy
            # gets rounded up to 1025 to be a multiple of the number of
            # required shares (since we use 25 out of 100 FEC).
            up = upload.Data(DATA, convergence=convergence)
            up.max_segment_size = 1024
            d1 = u.upload(up)
            return d1
        d.addCallback(_do_upload)
        def _upload_done(results):
            theuri = results.get_uri()
            log.msg("upload finished: uri is %s" % (theuri,))
            self.uri = theuri
            assert isinstance(self.uri, str), self.uri
            self.cap = uri.from_string(self.uri)
            self.n = self.clients[1].create_node_from_uri(self.uri)
        d.addCallback(_upload_done)

        def _upload_again(res):
            # Upload again. If using convergent encryption then this ought to be
            # short-circuited, however with the way we currently generate URIs
            # (i.e. because they include the roothash), we have to do all of the
            # encoding work, and only get to save on the upload part.
            log.msg("UPLOADING AGAIN")
            up = upload.Data(DATA, convergence=convergence)
            up.max_segment_size = 1024
            return self.uploader.upload(up)
        d.addCallback(_upload_again)

        def _download_to_data(res):
            log.msg("DOWNLOADING")
            return download_to_data(self.n)
        d.addCallback(_download_to_data)
        def _download_to_data_done(data):
            log.msg("download finished")
            self.failUnlessEqual(data, DATA)
        d.addCallback(_download_to_data_done)

        def _test_read(res):
            n = self.clients[1].create_node_from_uri(self.uri)
            d = download_to_data(n)
            def _read_done(data):
                self.failUnlessEqual(data, DATA)
            d.addCallback(_read_done)
            d.addCallback(lambda ign:
                          n.read(MemoryConsumer(), offset=1, size=4))
            def _read_portion_done(mc):
                self.failUnlessEqual("".join(mc.chunks), DATA[1:1+4])
            d.addCallback(_read_portion_done)
            d.addCallback(lambda ign:
                          n.read(MemoryConsumer(), offset=2, size=None))
            def _read_tail_done(mc):
                self.failUnlessEqual("".join(mc.chunks), DATA[2:])
            d.addCallback(_read_tail_done)
            d.addCallback(lambda ign:
                          n.read(MemoryConsumer(), size=len(DATA)+1000))
            def _read_too_much(mc):
                self.failUnlessEqual("".join(mc.chunks), DATA)
            d.addCallback(_read_too_much)

            return d
        d.addCallback(_test_read)

        def _test_bad_read(res):
            bad_u = uri.from_string_filenode(self.uri)
            bad_u.key = self.flip_bit(bad_u.key)
            bad_n = self.clients[1].create_node_from_uri(bad_u.to_string())
            # this should cause an error during download

            d = self.shouldFail2(NoSharesError, "'download bad node'",
                                 None,
                                 bad_n.read, MemoryConsumer(), offset=2)
            return d
        d.addCallback(_test_bad_read)

        def _download_nonexistent_uri(res):
            baduri = self.mangle_uri(self.uri)
            badnode = self.clients[1].create_node_from_uri(baduri)
            log.msg("about to download non-existent URI", level=log.UNUSUAL,
                    facility="tahoe.tests")
            d1 = download_to_data(badnode)
            def _baduri_should_fail(res):
                log.msg("finished downloading non-existent URI",
                        level=log.UNUSUAL, facility="tahoe.tests")
                self.failUnless(isinstance(res, Failure))
                self.failUnless(res.check(NoSharesError),
                                "expected NoSharesError, got %s" % res)
            d1.addBoth(_baduri_should_fail)
            return d1
        d.addCallback(_download_nonexistent_uri)

        # add a new node, which doesn't accept shares, and only uses the
        # helper for upload.
        d.addCallback(lambda res: self.add_extra_node(self.numclients,
                                                      self.helper_furl,
                                                      add_to_sparent=True))
        def _added(extra_node):
            self.extra_node = extra_node
            self.extra_node.encoding_params['happy'] = 5
        d.addCallback(_added)

        def _has_helper():
            uploader = self.extra_node.getServiceNamed("uploader")
            furl, connected = uploader.get_helper_info()
            return connected
        d.addCallback(lambda ign: self.poll(_has_helper))

        HELPER_DATA = "Data that needs help to upload" * 1000
        def _upload_with_helper(res):
            u = upload.Data(HELPER_DATA, convergence=convergence)
            d = self.extra_node.upload(u)
            def _uploaded(results):
                n = self.clients[1].create_node_from_uri(results.get_uri())
                return download_to_data(n)
            d.addCallback(_uploaded)
            def _check(newdata):
                self.failUnlessEqual(newdata, HELPER_DATA)
            d.addCallback(_check)
            return d
        d.addCallback(_upload_with_helper)

        def _upload_duplicate_with_helper(res):
            u = upload.Data(HELPER_DATA, convergence=convergence)
            u.debug_stash_RemoteEncryptedUploadable = True
            d = self.extra_node.upload(u)
            def _uploaded(results):
                n = self.clients[1].create_node_from_uri(results.get_uri())
                return download_to_data(n)
            d.addCallback(_uploaded)
            def _check(newdata):
                self.failUnlessEqual(newdata, HELPER_DATA)
                self.failIf(hasattr(u, "debug_RemoteEncryptedUploadable"),
                            "uploadable started uploading, should have been avoided")
            d.addCallback(_check)
            return d
        if convergence is not None:
            d.addCallback(_upload_duplicate_with_helper)

        d.addCallback(fireEventually)

        def _upload_resumable(res):
            DATA = "Data that needs help to upload and gets interrupted" * 1000
            u1 = CountingDataUploadable(DATA, convergence=convergence)
            u2 = CountingDataUploadable(DATA, convergence=convergence)

            # we interrupt the connection after about 5kB by shutting down
            # the helper, then restarting it.
            u1.interrupt_after = 5000
            u1.interrupt_after_d = defer.Deferred()
            bounced_d = defer.Deferred()
            def _do_bounce(res):
                d = self.bounce_client(0)
                d.addBoth(bounced_d.callback)
            u1.interrupt_after_d.addCallback(_do_bounce)

            # sneak into the helper and reduce its chunk size, so that our
            # debug_interrupt will sever the connection on about the fifth
            # chunk fetched. This makes sure that we've started to write the
            # new shares before we abandon them, which exercises the
            # abort/delete-partial-share code. TODO: find a cleaner way to do
            # this. I know that this will affect later uses of the helper in
            # this same test run, but I'm not currently worried about it.
            offloaded.CHKCiphertextFetcher.CHUNK_SIZE = 1000

            upload_d = self.extra_node.upload(u1)
            # The upload will start, and bounce_client() will be called after
            # about 5kB. bounced_d will fire after bounce_client() finishes
            # shutting down and restarting the node.
            d = bounced_d
            def _bounced(ign):
                # By this point, the upload should have failed because of the
                # interruption. upload_d will fire in a moment
                def _should_not_finish(res):
                    self.fail("interrupted upload should have failed, not"
                              " finished with result %s" % (res,))
                def _interrupted(f):
                    f.trap(DeadReferenceError)
                    # make sure we actually interrupted it before finishing
                    # the file
                    self.failUnless(u1.bytes_read < len(DATA),
                                    "read %d out of %d total" %
                                    (u1.bytes_read, len(DATA)))
                upload_d.addCallbacks(_should_not_finish, _interrupted)
                return upload_d
            d.addCallback(_bounced)

            def _disconnected(res):
                # check to make sure the storage servers aren't still hanging
                # on to the partial share: their incoming/ directories should
                # now be empty.
                log.msg("disconnected", level=log.NOISY,
                        facility="tahoe.test.test_system")
                for i in range(self.numclients):
                    incdir = os.path.join(self.getdir("client%d" % i),
                                          "storage", "shares", "incoming")
                    self.failIf(os.path.exists(incdir) and os.listdir(incdir))
            d.addCallback(_disconnected)

            d.addCallback(lambda res:
                          log.msg("wait_for_helper", level=log.NOISY,
                                  facility="tahoe.test.test_system"))
            # then we need to wait for the extra node to reestablish its
            # connection to the helper.
            d.addCallback(lambda ign: self.poll(_has_helper))

            d.addCallback(lambda res:
                          log.msg("uploading again", level=log.NOISY,
                                  facility="tahoe.test.test_system"))
            d.addCallback(lambda res: self.extra_node.upload(u2))

            def _uploaded(results):
                cap = results.get_uri()
                log.msg("Second upload complete", level=log.NOISY,
                        facility="tahoe.test.test_system")

                # this is really bytes received rather than sent, but it's
                # convenient and basically measures the same thing
                bytes_sent = results.get_ciphertext_fetched()
                self.failUnless(isinstance(bytes_sent, (int, long)), bytes_sent)

                # We currently don't support resumption of upload if the data is
                # encrypted with a random key.  (Because that would require us
                # to store the key locally and re-use it on the next upload of
                # this file, which isn't a bad thing to do, but we currently
                # don't do it.)
                if convergence is not None:
                    # Make sure we did not have to read the whole file the
                    # second time around .
                    self.failUnless(bytes_sent < len(DATA),
                                    "resumption didn't save us any work:"
                                    " read %r bytes out of %r total" %
                                    (bytes_sent, len(DATA)))
                else:
                    # Make sure we did have to read the whole file the second
                    # time around -- because the one that we partially uploaded
                    # earlier was encrypted with a different random key.
                    self.failIf(bytes_sent < len(DATA),
                                "resumption saved us some work even though we were using random keys:"
                                " read %r bytes out of %r total" %
                                (bytes_sent, len(DATA)))
                n = self.clients[1].create_node_from_uri(cap)
                return download_to_data(n)
            d.addCallback(_uploaded)

            def _check(newdata):
                self.failUnlessEqual(newdata, DATA)
                # If using convergent encryption, then also check that the
                # helper has removed the temp file from its directories.
                if convergence is not None:
                    basedir = os.path.join(self.getdir("client0"), "helper")
                    files = os.listdir(os.path.join(basedir, "CHK_encoding"))
                    self.failUnlessEqual(files, [])
                    files = os.listdir(os.path.join(basedir, "CHK_incoming"))
                    self.failUnlessEqual(files, [])
            d.addCallback(_check)
            return d
        d.addCallback(_upload_resumable)

        def _grab_stats(ignored):
            # the StatsProvider doesn't normally publish a FURL:
            # instead it passes a live reference to the StatsGatherer
            # (if and when it connects). To exercise the remote stats
            # interface, we manually publish client0's StatsProvider
            # and use client1 to query it.
            sp = self.clients[0].stats_provider
            sp_furl = self.clients[0].tub.registerReference(sp)
            d = self.clients[1].tub.getReference(sp_furl)
            d.addCallback(lambda sp_rref: sp_rref.callRemote("get_stats"))
            def _got_stats(stats):
                #print "STATS"
                #from pprint import pprint
                #pprint(stats)
                s = stats["stats"]
                self.failUnlessEqual(s["storage_server.accepting_immutable_shares"], 1)
                c = stats["counters"]
                self.failUnless("storage_server.allocate" in c)
            d.addCallback(_got_stats)
            return d
        d.addCallback(_grab_stats)

        return d

    def _find_all_shares(self, basedir):
        shares = []
        for (dirpath, dirnames, filenames) in os.walk(basedir):
            if "storage" not in dirpath:
                continue
            if not filenames:
                continue
            pieces = dirpath.split(os.sep)
            if (len(pieces) >= 5
                and pieces[-4] == "storage"
                and pieces[-3] == "shares"):
                # we're sitting in .../storage/shares/$START/$SINDEX , and there
                # are sharefiles here
                assert pieces[-5].startswith("client")
                client_num = int(pieces[-5][-1])
                storage_index_s = pieces[-1]
                storage_index = si_a2b(storage_index_s)
                for sharename in filenames:
                    shnum = int(sharename)
                    filename = os.path.join(dirpath, sharename)
                    data = (client_num, storage_index, filename, shnum)
                    shares.append(data)
        if not shares:
            self.fail("unable to find any share files in %s" % basedir)
        return shares

    def _corrupt_mutable_share(self, filename, which):
        msf = MutableShareFile(filename)
        datav = msf.readv([ (0, 1000000) ])
        final_share = datav[0]
        assert len(final_share) < 1000000 # ought to be truncated
        pieces = mutable_layout.unpack_share(final_share)
        (seqnum, root_hash, IV, k, N, segsize, datalen,
         verification_key, signature, share_hash_chain, block_hash_tree,
         share_data, enc_privkey) = pieces

        if which == "seqnum":
            seqnum = seqnum + 15
        elif which == "R":
            root_hash = self.flip_bit(root_hash)
        elif which == "IV":
            IV = self.flip_bit(IV)
        elif which == "segsize":
            segsize = segsize + 15
        elif which == "pubkey":
            verification_key = self.flip_bit(verification_key)
        elif which == "signature":
            signature = self.flip_bit(signature)
        elif which == "share_hash_chain":
            nodenum = share_hash_chain.keys()[0]
            share_hash_chain[nodenum] = self.flip_bit(share_hash_chain[nodenum])
        elif which == "block_hash_tree":
            block_hash_tree[-1] = self.flip_bit(block_hash_tree[-1])
        elif which == "share_data":
            share_data = self.flip_bit(share_data)
        elif which == "encprivkey":
            enc_privkey = self.flip_bit(enc_privkey)

        prefix = mutable_layout.pack_prefix(seqnum, root_hash, IV, k, N,
                                            segsize, datalen)
        final_share = mutable_layout.pack_share(prefix,
                                                verification_key,
                                                signature,
                                                share_hash_chain,
                                                block_hash_tree,
                                                share_data,
                                                enc_privkey)
        msf.writev( [(0, final_share)], None)


    def test_mutable(self):
        self.basedir = "system/SystemTest/test_mutable"
        DATA = "initial contents go here."  # 25 bytes % 3 != 0
        DATA_uploadable = MutableData(DATA)
        NEWDATA = "new contents yay"
        NEWDATA_uploadable = MutableData(NEWDATA)
        NEWERDATA = "this is getting old"
        NEWERDATA_uploadable = MutableData(NEWERDATA)

        d = self.set_up_nodes()

        def _create_mutable(res):
            c = self.clients[0]
            log.msg("starting create_mutable_file")
            d1 = c.create_mutable_file(DATA_uploadable)
            def _done(res):
                log.msg("DONE: %s" % (res,))
                self._mutable_node_1 = res
            d1.addCallback(_done)
            return d1
        d.addCallback(_create_mutable)

        @defer.inlineCallbacks
        def _test_debug(res):
            # find a share. It is important to run this while there is only
            # one slot in the grid.
            shares = self._find_all_shares(self.basedir)
            (client_num, storage_index, filename, shnum) = shares[0]
            log.msg("test_system.SystemTest.test_mutable._test_debug using %s"
                    % filename)
            log.msg(" for clients[%d]" % client_num)

            rc,output,err = yield run_cli("debug", "dump-share", "--offsets",
                                          filename)
            self.failUnlessEqual(rc, 0)
            try:
                self.failUnless("Mutable slot found:\n" in output)
                self.failUnless("share_type: SDMF\n" in output)
                peerid = idlib.nodeid_b2a(self.clients[client_num].nodeid)
                self.failUnless(" WE for nodeid: %s\n" % peerid in output)
                self.failUnless(" num_extra_leases: 0\n" in output)
                self.failUnless("  secrets are for nodeid: %s\n" % peerid
                                in output)
                self.failUnless(" SDMF contents:\n" in output)
                self.failUnless("  seqnum: 1\n" in output)
                self.failUnless("  required_shares: 3\n" in output)
                self.failUnless("  total_shares: 10\n" in output)
                self.failUnless("  segsize: 27\n" in output, (output, filename))
                self.failUnless("  datalen: 25\n" in output)
                # the exact share_hash_chain nodes depends upon the sharenum,
                # and is more of a hassle to compute than I want to deal with
                # now
                self.failUnless("  share_hash_chain: " in output)
                self.failUnless("  block_hash_tree: 1 nodes\n" in output)
                expected = ("  verify-cap: URI:SSK-Verifier:%s:" %
                            base32.b2a(storage_index))
                self.failUnless(expected in output)
            except unittest.FailTest:
                print
                print "dump-share output was:"
                print output
                raise
        d.addCallback(_test_debug)

        # test retrieval

        # first, let's see if we can use the existing node to retrieve the
        # contents. This allows it to use the cached pubkey and maybe the
        # latest-known sharemap.

        d.addCallback(lambda res: self._mutable_node_1.download_best_version())
        def _check_download_1(res):
            self.failUnlessEqual(res, DATA)
            # now we see if we can retrieve the data from a new node,
            # constructed using the URI of the original one. We do this test
            # on the same client that uploaded the data.
            uri = self._mutable_node_1.get_uri()
            log.msg("starting retrieve1")
            newnode = self.clients[0].create_node_from_uri(uri)
            newnode_2 = self.clients[0].create_node_from_uri(uri)
            self.failUnlessIdentical(newnode, newnode_2)
            return newnode.download_best_version()
        d.addCallback(_check_download_1)

        def _check_download_2(res):
            self.failUnlessEqual(res, DATA)
            # same thing, but with a different client
            uri = self._mutable_node_1.get_uri()
            newnode = self.clients[1].create_node_from_uri(uri)
            log.msg("starting retrieve2")
            d1 = newnode.download_best_version()
            d1.addCallback(lambda res: (res, newnode))
            return d1
        d.addCallback(_check_download_2)

        def _check_download_3((res, newnode)):
            self.failUnlessEqual(res, DATA)
            # replace the data
            log.msg("starting replace1")
            d1 = newnode.overwrite(NEWDATA_uploadable)
            d1.addCallback(lambda res: newnode.download_best_version())
            return d1
        d.addCallback(_check_download_3)

        def _check_download_4(res):
            self.failUnlessEqual(res, NEWDATA)
            # now create an even newer node and replace the data on it. This
            # new node has never been used for download before.
            uri = self._mutable_node_1.get_uri()
            newnode1 = self.clients[2].create_node_from_uri(uri)
            newnode2 = self.clients[3].create_node_from_uri(uri)
            self._newnode3 = self.clients[3].create_node_from_uri(uri)
            log.msg("starting replace2")
            d1 = newnode1.overwrite(NEWERDATA_uploadable)
            d1.addCallback(lambda res: newnode2.download_best_version())
            return d1
        d.addCallback(_check_download_4)

        def _check_download_5(res):
            log.msg("finished replace2")
            self.failUnlessEqual(res, NEWERDATA)
        d.addCallback(_check_download_5)

        def _corrupt_shares(res):
            # run around and flip bits in all but k of the shares, to test
            # the hash checks
            shares = self._find_all_shares(self.basedir)
            ## sort by share number
            #shares.sort( lambda a,b: cmp(a[3], b[3]) )
            where = dict([ (shnum, filename)
                           for (client_num, storage_index, filename, shnum)
                           in shares ])
            assert len(where) == 10 # this test is designed for 3-of-10
            for shnum, filename in where.items():
                # shares 7,8,9 are left alone. read will check
                # (share_hash_chain, block_hash_tree, share_data). New
                # seqnum+R pairs will trigger a check of (seqnum, R, IV,
                # segsize, signature).
                if shnum == 0:
                    # read: this will trigger "pubkey doesn't match
                    # fingerprint".
                    self._corrupt_mutable_share(filename, "pubkey")
                    self._corrupt_mutable_share(filename, "encprivkey")
                elif shnum == 1:
                    # triggers "signature is invalid"
                    self._corrupt_mutable_share(filename, "seqnum")
                elif shnum == 2:
                    # triggers "signature is invalid"
                    self._corrupt_mutable_share(filename, "R")
                elif shnum == 3:
                    # triggers "signature is invalid"
                    self._corrupt_mutable_share(filename, "segsize")
                elif shnum == 4:
                    self._corrupt_mutable_share(filename, "share_hash_chain")
                elif shnum == 5:
                    self._corrupt_mutable_share(filename, "block_hash_tree")
                elif shnum == 6:
                    self._corrupt_mutable_share(filename, "share_data")
                # other things to correct: IV, signature
                # 7,8,9 are left alone

                # note that initial_query_count=5 means that we'll hit the
                # first 5 servers in effectively random order (based upon
                # response time), so we won't necessarily ever get a "pubkey
                # doesn't match fingerprint" error (if we hit shnum>=1 before
                # shnum=0, we pull the pubkey from there). To get repeatable
                # specific failures, we need to set initial_query_count=1,
                # but of course that will change the sequencing behavior of
                # the retrieval process. TODO: find a reasonable way to make
                # this a parameter, probably when we expand this test to test
                # for one failure mode at a time.

                # when we retrieve this, we should get three signature
                # failures (where we've mangled seqnum, R, and segsize). The
                # pubkey mangling
        d.addCallback(_corrupt_shares)

        d.addCallback(lambda res: self._newnode3.download_best_version())
        d.addCallback(_check_download_5)

        def _check_empty_file(res):
            # make sure we can create empty files, this usually screws up the
            # segsize math
            d1 = self.clients[2].create_mutable_file(MutableData(""))
            d1.addCallback(lambda newnode: newnode.download_best_version())
            d1.addCallback(lambda res: self.failUnlessEqual("", res))
            return d1
        d.addCallback(_check_empty_file)

        d.addCallback(lambda res: self.clients[0].create_dirnode())
        def _created_dirnode(dnode):
            log.msg("_created_dirnode(%s)" % (dnode,))
            d1 = dnode.list()
            d1.addCallback(lambda children: self.failUnlessEqual(children, {}))
            d1.addCallback(lambda res: dnode.has_child(u"edgar"))
            d1.addCallback(lambda answer: self.failUnlessEqual(answer, False))
            d1.addCallback(lambda res: dnode.set_node(u"see recursive", dnode))
            d1.addCallback(lambda res: dnode.has_child(u"see recursive"))
            d1.addCallback(lambda answer: self.failUnlessEqual(answer, True))
            d1.addCallback(lambda res: dnode.build_manifest().when_done())
            d1.addCallback(lambda res:
                           self.failUnlessEqual(len(res["manifest"]), 1))
            return d1
        d.addCallback(_created_dirnode)

        return d

    def flip_bit(self, good):
        return good[:-1] + chr(ord(good[-1]) ^ 0x01)

    def mangle_uri(self, gooduri):
        # change the key, which changes the storage index, which means we'll
        # be asking about the wrong file, so nobody will have any shares
        u = uri.from_string(gooduri)
        u2 = uri.CHKFileURI(key=self.flip_bit(u.key),
                            uri_extension_hash=u.uri_extension_hash,
                            needed_shares=u.needed_shares,
                            total_shares=u.total_shares,
                            size=u.size)
        return u2.to_string()

    # TODO: add a test which mangles the uri_extension_hash instead, and
    # should fail due to not being able to get a valid uri_extension block.
    # Also a test which sneakily mangles the uri_extension block to change
    # some of the validation data, so it will fail in the post-download phase
    # when the file's crypttext integrity check fails. Do the same thing for
    # the key, which should cause the download to fail the post-download
    # plaintext_hash check.

    def test_filesystem(self):
        self.basedir = "system/SystemTest/test_filesystem"
        self.data = LARGE_DATA
        d = self.set_up_nodes(use_stats_gatherer=True)
        def _new_happy_semantics(ign):
            for c in self.clients:
                c.encoding_params['happy'] = 1
        d.addCallback(_new_happy_semantics)
        d.addCallback(self._test_introweb)
        d.addCallback(self.log, "starting publish")
        d.addCallback(self._do_publish1)
        d.addCallback(self._test_runner)
        d.addCallback(self._do_publish2)
        # at this point, we have the following filesystem (where "R" denotes
        # self._root_directory_uri):
        # R
        # R/subdir1
        # R/subdir1/mydata567
        # R/subdir1/subdir2/
        # R/subdir1/subdir2/mydata992

        d.addCallback(lambda res: self.bounce_client(0))
        d.addCallback(self.log, "bounced client0")

        d.addCallback(self._check_publish1)
        d.addCallback(self.log, "did _check_publish1")
        d.addCallback(self._check_publish2)
        d.addCallback(self.log, "did _check_publish2")
        d.addCallback(self._do_publish_private)
        d.addCallback(self.log, "did _do_publish_private")
        # now we also have (where "P" denotes a new dir):
        #  P/personal/sekrit data
        #  P/s2-rw -> /subdir1/subdir2/
        #  P/s2-ro -> /subdir1/subdir2/ (read-only)
        d.addCallback(self._check_publish_private)
        d.addCallback(self.log, "did _check_publish_private")
        d.addCallback(self._test_web)
        d.addCallback(self._test_control)
        d.addCallback(self._test_cli)
        # P now has four top-level children:
        # P/personal/sekrit data
        # P/s2-ro/
        # P/s2-rw/
        # P/test_put/  (empty)
        d.addCallback(self._test_checker)
        return d

    def _test_introweb(self, res):
        d = getPage(self.introweb_url, method="GET", followRedirect=True)
        def _check(res):
            try:
                self.failUnless("%s: %s" % (allmydata.__appname__, allmydata.__version__) in res)
                verstr = str(allmydata.__version__)

                # The Python "rational version numbering" convention
                # disallows "-r$REV" but allows ".post$REV"
                # instead. Eventually we'll probably move to
                # that. When we do, this test won't go red:
                ix = verstr.rfind('-r')
                if ix != -1:
                    altverstr = verstr[:ix] + '.post' + verstr[ix+2:]
                else:
                    ix = verstr.rfind('.post')
                    if ix != -1:
                        altverstr = verstr[:ix] + '-r' + verstr[ix+5:]
                    else:
                        altverstr = verstr

                appverstr = "%s: %s" % (allmydata.__appname__, verstr)
                newappverstr = "%s: %s" % (allmydata.__appname__, altverstr)

                self.failUnless((appverstr in res) or (newappverstr in res), (appverstr, newappverstr, res))
                self.failUnless("Announcement Summary: storage: 5" in res)
                self.failUnless("Subscription Summary: storage: 5" in res)
                self.failUnless("tahoe.css" in res)
            except unittest.FailTest:
                print
                print "GET %s output was:" % self.introweb_url
                print res
                raise
        d.addCallback(_check)
        # make sure it serves the CSS too
        d.addCallback(lambda res:
                      getPage(self.introweb_url+"tahoe.css", method="GET"))
        d.addCallback(lambda res:
                      getPage(self.introweb_url + "?t=json",
                              method="GET", followRedirect=True))
        def _check_json(res):
            data = simplejson.loads(res)
            try:
                self.failUnlessEqual(data["subscription_summary"],
                                     {"storage": 5})
                self.failUnlessEqual(data["announcement_summary"],
                                     {"storage": 5})
            except unittest.FailTest:
                print
                print "GET %s?t=json output was:" % self.introweb_url
                print res
                raise
        d.addCallback(_check_json)
        return d

    def _do_publish1(self, res):
        ut = upload.Data(self.data, convergence=None)
        c0 = self.clients[0]
        d = c0.create_dirnode()
        def _made_root(new_dirnode):
            self._root_directory_uri = new_dirnode.get_uri()
            return c0.create_node_from_uri(self._root_directory_uri)
        d.addCallback(_made_root)
        d.addCallback(lambda root: root.create_subdirectory(u"subdir1"))
        def _made_subdir1(subdir1_node):
            self._subdir1_node = subdir1_node
            d1 = subdir1_node.add_file(u"mydata567", ut)
            d1.addCallback(self.log, "publish finished")
            def _stash_uri(filenode):
                self.uri = filenode.get_uri()
                assert isinstance(self.uri, str), (self.uri, filenode)
            d1.addCallback(_stash_uri)
            return d1
        d.addCallback(_made_subdir1)
        return d

    def _do_publish2(self, res):
        ut = upload.Data(self.data, convergence=None)
        d = self._subdir1_node.create_subdirectory(u"subdir2")
        d.addCallback(lambda subdir2: subdir2.add_file(u"mydata992", ut))
        return d

    def log(self, res, *args, **kwargs):
        # print "MSG: %s  RES: %s" % (msg, args)
        log.msg(*args, **kwargs)
        return res

    def _do_publish_private(self, res):
        self.smalldata = "sssh, very secret stuff"
        ut = upload.Data(self.smalldata, convergence=None)
        d = self.clients[0].create_dirnode()
        d.addCallback(self.log, "GOT private directory")
        def _got_new_dir(privnode):
            rootnode = self.clients[0].create_node_from_uri(self._root_directory_uri)
            d1 = privnode.create_subdirectory(u"personal")
            d1.addCallback(self.log, "made P/personal")
            d1.addCallback(lambda node: node.add_file(u"sekrit data", ut))
            d1.addCallback(self.log, "made P/personal/sekrit data")
            d1.addCallback(lambda res: rootnode.get_child_at_path([u"subdir1", u"subdir2"]))
            def _got_s2(s2node):
                d2 = privnode.set_uri(u"s2-rw", s2node.get_uri(),
                                      s2node.get_readonly_uri())
                d2.addCallback(lambda node:
                               privnode.set_uri(u"s2-ro",
                                                s2node.get_readonly_uri(),
                                                s2node.get_readonly_uri()))
                return d2
            d1.addCallback(_got_s2)
            d1.addCallback(lambda res: privnode)
            return d1
        d.addCallback(_got_new_dir)
        return d

    def _check_publish1(self, res):
        # this one uses the iterative API
        c1 = self.clients[1]
        d = defer.succeed(c1.create_node_from_uri(self._root_directory_uri))
        d.addCallback(self.log, "check_publish1 got /")
        d.addCallback(lambda root: root.get(u"subdir1"))
        d.addCallback(lambda subdir1: subdir1.get(u"mydata567"))
        d.addCallback(lambda filenode: download_to_data(filenode))
        d.addCallback(self.log, "get finished")
        def _get_done(data):
            self.failUnlessEqual(data, self.data)
        d.addCallback(_get_done)
        return d

    def _check_publish2(self, res):
        # this one uses the path-based API
        rootnode = self.clients[1].create_node_from_uri(self._root_directory_uri)
        d = rootnode.get_child_at_path(u"subdir1")
        d.addCallback(lambda dirnode:
                      self.failUnless(IDirectoryNode.providedBy(dirnode)))
        d.addCallback(lambda res: rootnode.get_child_at_path(u"subdir1/mydata567"))
        d.addCallback(lambda filenode: download_to_data(filenode))
        d.addCallback(lambda data: self.failUnlessEqual(data, self.data))

        d.addCallback(lambda res: rootnode.get_child_at_path(u"subdir1/mydata567"))
        def _got_filenode(filenode):
            fnode = self.clients[1].create_node_from_uri(filenode.get_uri())
            assert fnode == filenode
        d.addCallback(_got_filenode)
        return d

    def _check_publish_private(self, resnode):
        # this one uses the path-based API
        self._private_node = resnode

        d = self._private_node.get_child_at_path(u"personal")
        def _got_personal(personal):
            self._personal_node = personal
            return personal
        d.addCallback(_got_personal)

        d.addCallback(lambda dirnode:
                      self.failUnless(IDirectoryNode.providedBy(dirnode), dirnode))
        def get_path(path):
            return self._private_node.get_child_at_path(path)

        d.addCallback(lambda res: get_path(u"personal/sekrit data"))
        d.addCallback(lambda filenode: download_to_data(filenode))
        d.addCallback(lambda data: self.failUnlessEqual(data, self.smalldata))
        d.addCallback(lambda res: get_path(u"s2-rw"))
        d.addCallback(lambda dirnode: self.failUnless(dirnode.is_mutable()))
        d.addCallback(lambda res: get_path(u"s2-ro"))
        def _got_s2ro(dirnode):
            self.failUnless(dirnode.is_mutable(), dirnode)
            self.failUnless(dirnode.is_readonly(), dirnode)
            d1 = defer.succeed(None)
            d1.addCallback(lambda res: dirnode.list())
            d1.addCallback(self.log, "dirnode.list")

            d1.addCallback(lambda res: self.shouldFail2(NotWriteableError, "mkdir(nope)", None, dirnode.create_subdirectory, u"nope"))

            d1.addCallback(self.log, "doing add_file(ro)")
            ut = upload.Data("I will disappear, unrecorded and unobserved. The tragedy of my demise is made more poignant by its silence, but this beauty is not for you to ever know.", convergence="99i-p1x4-xd4-18yc-ywt-87uu-msu-zo -- completely and totally unguessable string (unless you read this)")
            d1.addCallback(lambda res: self.shouldFail2(NotWriteableError, "add_file(nope)", None, dirnode.add_file, u"hope", ut))

            d1.addCallback(self.log, "doing get(ro)")
            d1.addCallback(lambda res: dirnode.get(u"mydata992"))
            d1.addCallback(lambda filenode:
                           self.failUnless(IFileNode.providedBy(filenode)))

            d1.addCallback(self.log, "doing delete(ro)")
            d1.addCallback(lambda res: self.shouldFail2(NotWriteableError, "delete(nope)", None, dirnode.delete, u"mydata992"))

            d1.addCallback(lambda res: self.shouldFail2(NotWriteableError, "set_uri(nope)", None, dirnode.set_uri, u"hopeless", self.uri, self.uri))

            d1.addCallback(lambda res: self.shouldFail2(NoSuchChildError, "get(missing)", "missing", dirnode.get, u"missing"))

            personal = self._personal_node
            d1.addCallback(lambda res: self.shouldFail2(NotWriteableError, "mv from readonly", None, dirnode.move_child_to, u"mydata992", personal, u"nope"))

            d1.addCallback(self.log, "doing move_child_to(ro)2")
            d1.addCallback(lambda res: self.shouldFail2(NotWriteableError, "mv to readonly", None, personal.move_child_to, u"sekrit data", dirnode, u"nope"))

            d1.addCallback(self.log, "finished with _got_s2ro")
            return d1
        d.addCallback(_got_s2ro)
        def _got_home(dummy):
            home = self._private_node
            personal = self._personal_node
            d1 = defer.succeed(None)
            d1.addCallback(self.log, "mv 'P/personal/sekrit data' to P/sekrit")
            d1.addCallback(lambda res:
                           personal.move_child_to(u"sekrit data",home,u"sekrit"))

            d1.addCallback(self.log, "mv P/sekrit 'P/sekrit data'")
            d1.addCallback(lambda res:
                           home.move_child_to(u"sekrit", home, u"sekrit data"))

            d1.addCallback(self.log, "mv 'P/sekret data' P/personal/")
            d1.addCallback(lambda res:
                           home.move_child_to(u"sekrit data", personal))

            d1.addCallback(lambda res: home.build_manifest().when_done())
            d1.addCallback(self.log, "manifest")
            #  five items:
            # P/
            # P/personal/
            # P/personal/sekrit data
            # P/s2-rw  (same as P/s2-ro)
            # P/s2-rw/mydata992 (same as P/s2-rw/mydata992)
            d1.addCallback(lambda res:
                           self.failUnlessEqual(len(res["manifest"]), 5))
            d1.addCallback(lambda res: home.start_deep_stats().when_done())
            def _check_stats(stats):
                expected = {"count-immutable-files": 1,
                            "count-mutable-files": 0,
                            "count-literal-files": 1,
                            "count-files": 2,
                            "count-directories": 3,
                            "size-immutable-files": 112,
                            "size-literal-files": 23,
                            #"size-directories": 616, # varies
                            #"largest-directory": 616,
                            "largest-directory-children": 3,
                            "largest-immutable-file": 112,
                            }
                for k,v in expected.iteritems():
                    self.failUnlessEqual(stats[k], v,
                                         "stats[%s] was %s, not %s" %
                                         (k, stats[k], v))
                self.failUnless(stats["size-directories"] > 1300,
                                stats["size-directories"])
                self.failUnless(stats["largest-directory"] > 800,
                                stats["largest-directory"])
                self.failUnlessEqual(stats["size-files-histogram"],
                                     [ (11, 31, 1), (101, 316, 1) ])
            d1.addCallback(_check_stats)
            return d1
        d.addCallback(_got_home)
        return d

    def shouldFail(self, res, expected_failure, which, substring=None):
        if isinstance(res, Failure):
            res.trap(expected_failure)
            if substring:
                self.failUnless(substring in str(res),
                                "substring '%s' not in '%s'"
                                % (substring, str(res)))
        else:
            self.fail("%s was supposed to raise %s, not get '%s'" %
                      (which, expected_failure, res))

    def shouldFail2(self, expected_failure, which, substring, callable, *args, **kwargs):
        assert substring is None or isinstance(substring, str)
        d = defer.maybeDeferred(callable, *args, **kwargs)
        def done(res):
            if isinstance(res, Failure):
                res.trap(expected_failure)
                if substring:
                    self.failUnless(substring in str(res),
                                    "substring '%s' not in '%s'"
                                    % (substring, str(res)))
            else:
                self.fail("%s was supposed to raise %s, not get '%s'" %
                          (which, expected_failure, res))
        d.addBoth(done)
        return d

    def PUT(self, urlpath, data):
        url = self.webish_url + urlpath
        return getPage(url, method="PUT", postdata=data)

    def GET(self, urlpath, followRedirect=False):
        url = self.webish_url + urlpath
        return getPage(url, method="GET", followRedirect=followRedirect)

    def POST(self, urlpath, followRedirect=False, use_helper=False, **fields):
        sepbase = "boogabooga"
        sep = "--" + sepbase
        form = []
        form.append(sep)
        form.append('Content-Disposition: form-data; name="_charset"')
        form.append('')
        form.append('UTF-8')
        form.append(sep)
        for name, value in fields.iteritems():
            if isinstance(value, tuple):
                filename, value = value
                form.append('Content-Disposition: form-data; name="%s"; '
                            'filename="%s"' % (name, filename.encode("utf-8")))
            else:
                form.append('Content-Disposition: form-data; name="%s"' % name)
            form.append('')
            form.append(str(value))
            form.append(sep)
        form[-1] += "--"
        body = ""
        headers = {}
        if fields:
            body = "\r\n".join(form) + "\r\n"
            headers["content-type"] = "multipart/form-data; boundary=%s" % sepbase
        return self.POST2(urlpath, body, headers, followRedirect, use_helper)

    def POST2(self, urlpath, body="", headers={}, followRedirect=False,
              use_helper=False):
        if use_helper:
            url = self.helper_webish_url + urlpath
        else:
            url = self.webish_url + urlpath
        return getPage(url, method="POST", postdata=body, headers=headers,
                       followRedirect=followRedirect)

    def _test_web(self, res):
        base = self.webish_url
        public = "uri/" + self._root_directory_uri
        d = getPage(base)
        def _got_welcome(page):
            html = page.replace('\n', ' ')
            connected_re = r'Connected to <span>%d</span>\s*of <span>%d</span> known storage servers' % (self.numclients, self.numclients)
            self.failUnless(re.search(connected_re, html),
                            "I didn't see the right '%s' message in:\n%s" % (connected_re, page))
            # nodeids/tubids don't have any regexp-special characters
            nodeid_re = r'<th>Node ID:</th>\s*<td title="TubID: %s">%s</td>' % (
                self.clients[0].get_long_tubid(), self.clients[0].get_long_nodeid())
            self.failUnless(re.search(nodeid_re, html),
                            "I didn't see the right '%s' message in:\n%s" % (nodeid_re, page))
            self.failUnless("Helper: 0 active uploads" in page)
        d.addCallback(_got_welcome)
        d.addCallback(self.log, "done with _got_welcome")

        # get the welcome page from the node that uses the helper too
        d.addCallback(lambda res: getPage(self.helper_webish_url))
        def _got_welcome_helper(page):
            html = page.replace('\n', ' ')
            self.failUnless(re.search('<img (src="img/connected-yes.png" |alt="Connected" ){2}/>', html), page)
            self.failUnlessIn("Not running helper", page)
        d.addCallback(_got_welcome_helper)

        d.addCallback(lambda res: getPage(base + public))
        d.addCallback(lambda res: getPage(base + public + "/subdir1"))
        def _got_subdir1(page):
            # there ought to be an href for our file
            self.failUnlessIn('<td align="right">%d</td>' % len(self.data), page)
            self.failUnless(">mydata567</a>" in page)
        d.addCallback(_got_subdir1)
        d.addCallback(self.log, "done with _got_subdir1")
        d.addCallback(lambda res:
                      getPage(base + public + "/subdir1/mydata567"))
        def _got_data(page):
            self.failUnlessEqual(page, self.data)
        d.addCallback(_got_data)

        # download from a URI embedded in a URL
        d.addCallback(self.log, "_get_from_uri")
        def _get_from_uri(res):
            return getPage(base + "uri/%s?filename=%s"
                           % (self.uri, "mydata567"))
        d.addCallback(_get_from_uri)
        def _got_from_uri(page):
            self.failUnlessEqual(page, self.data)
        d.addCallback(_got_from_uri)

        # download from a URI embedded in a URL, second form
        d.addCallback(self.log, "_get_from_uri2")
        def _get_from_uri2(res):
            return getPage(base + "uri?uri=%s" % (self.uri,))
        d.addCallback(_get_from_uri2)
        d.addCallback(_got_from_uri)

        # download from a bogus URI, make sure we get a reasonable error
        d.addCallback(self.log, "_get_from_bogus_uri", level=log.UNUSUAL)
        def _get_from_bogus_uri(res):
            d1 = getPage(base + "uri/%s?filename=%s"
                         % (self.mangle_uri(self.uri), "mydata567"))
            d1.addBoth(self.shouldFail, Error, "downloading bogus URI",
                       "410")
            return d1
        d.addCallback(_get_from_bogus_uri)
        d.addCallback(self.log, "_got_from_bogus_uri", level=log.UNUSUAL)

        # upload a file with PUT
        d.addCallback(self.log, "about to try PUT")
        d.addCallback(lambda res: self.PUT(public + "/subdir3/new.txt",
                                           "new.txt contents"))
        d.addCallback(lambda res: self.GET(public + "/subdir3/new.txt"))
        d.addCallback(self.failUnlessEqual, "new.txt contents")
        # and again with something large enough to use multiple segments,
        # and hopefully trigger pauseProducing too
        def _new_happy_semantics(ign):
            for c in self.clients:
                # these get reset somewhere? Whatever.
                c.encoding_params['happy'] = 1
        d.addCallback(_new_happy_semantics)
        d.addCallback(lambda res: self.PUT(public + "/subdir3/big.txt",
                                           "big" * 500000)) # 1.5MB
        d.addCallback(lambda res: self.GET(public + "/subdir3/big.txt"))
        d.addCallback(lambda res: self.failUnlessEqual(len(res), 1500000))

        # can we replace files in place?
        d.addCallback(lambda res: self.PUT(public + "/subdir3/new.txt",
                                           "NEWER contents"))
        d.addCallback(lambda res: self.GET(public + "/subdir3/new.txt"))
        d.addCallback(self.failUnlessEqual, "NEWER contents")

        # test unlinked POST
        d.addCallback(lambda res: self.POST("uri", t="upload",
                                            file=("new.txt", "data" * 10000)))
        # and again using the helper, which exercises different upload-status
        # display code
        d.addCallback(lambda res: self.POST("uri", use_helper=True, t="upload",
                                            file=("foo.txt", "data2" * 10000)))

        # check that the status page exists
        d.addCallback(lambda res: self.GET("status", followRedirect=True))
        def _got_status(res):
            # find an interesting upload and download to look at. LIT files
            # are not interesting.
            h = self.clients[0].get_history()
            for ds in h.list_all_download_statuses():
                if ds.get_size() > 200:
                    self._down_status = ds.get_counter()
            for us in h.list_all_upload_statuses():
                if us.get_size() > 200:
                    self._up_status = us.get_counter()
            rs = list(h.list_all_retrieve_statuses())[0]
            self._retrieve_status = rs.get_counter()
            ps = list(h.list_all_publish_statuses())[0]
            self._publish_status = ps.get_counter()
            us = list(h.list_all_mapupdate_statuses())[0]
            self._update_status = us.get_counter()

            # and that there are some upload- and download- status pages
            return self.GET("status/up-%d" % self._up_status)
        d.addCallback(_got_status)
        def _got_up(res):
            return self.GET("status/down-%d" % self._down_status)
        d.addCallback(_got_up)
        def _got_down(res):
            return self.GET("status/mapupdate-%d" % self._update_status)
        d.addCallback(_got_down)
        def _got_update(res):
            return self.GET("status/publish-%d" % self._publish_status)
        d.addCallback(_got_update)
        def _got_publish(res):
            self.failUnlessIn("Publish Results", res)
            return self.GET("status/retrieve-%d" % self._retrieve_status)
        d.addCallback(_got_publish)
        def _got_retrieve(res):
            self.failUnlessIn("Retrieve Results", res)
        d.addCallback(_got_retrieve)

        # check that the helper status page exists
        d.addCallback(lambda res:
                      self.GET("helper_status", followRedirect=True))
        def _got_helper_status(res):
            self.failUnless("Bytes Fetched:" in res)
            # touch a couple of files in the helper's working directory to
            # exercise more code paths
            workdir = os.path.join(self.getdir("client0"), "helper")
            incfile = os.path.join(workdir, "CHK_incoming", "spurious")
            f = open(incfile, "wb")
            f.write("small file")
            f.close()
            then = time.time() - 86400*3
            now = time.time()
            os.utime(incfile, (now, then))
            encfile = os.path.join(workdir, "CHK_encoding", "spurious")
            f = open(encfile, "wb")
            f.write("less small file")
            f.close()
            os.utime(encfile, (now, then))
        d.addCallback(_got_helper_status)
        # and that the json form exists
        d.addCallback(lambda res:
                      self.GET("helper_status?t=json", followRedirect=True))
        def _got_helper_status_json(res):
            data = simplejson.loads(res)
            self.failUnlessEqual(data["chk_upload_helper.upload_need_upload"],
                                 1)
            self.failUnlessEqual(data["chk_upload_helper.incoming_count"], 1)
            self.failUnlessEqual(data["chk_upload_helper.incoming_size"], 10)
            self.failUnlessEqual(data["chk_upload_helper.incoming_size_old"],
                                 10)
            self.failUnlessEqual(data["chk_upload_helper.encoding_count"], 1)
            self.failUnlessEqual(data["chk_upload_helper.encoding_size"], 15)
            self.failUnlessEqual(data["chk_upload_helper.encoding_size_old"],
                                 15)
        d.addCallback(_got_helper_status_json)

        # and check that client[3] (which uses a helper but does not run one
        # itself) doesn't explode when you ask for its status
        d.addCallback(lambda res: getPage(self.helper_webish_url + "status/"))
        def _got_non_helper_status(res):
            self.failUnlessIn("Recent and Active Operations", res)
        d.addCallback(_got_non_helper_status)

        # or for helper status with t=json
        d.addCallback(lambda res:
                      getPage(self.helper_webish_url + "helper_status?t=json"))
        def _got_non_helper_status_json(res):
            data = simplejson.loads(res)
            self.failUnlessEqual(data, {})
        d.addCallback(_got_non_helper_status_json)

        # see if the statistics page exists
        d.addCallback(lambda res: self.GET("statistics"))
        def _got_stats(res):
            self.failUnlessIn("Operational Statistics", res)
            self.failUnlessIn("  'downloader.files_downloaded': 5,", res)
        d.addCallback(_got_stats)
        d.addCallback(lambda res: self.GET("statistics?t=json"))
        def _got_stats_json(res):
            data = simplejson.loads(res)
            self.failUnlessEqual(data["counters"]["uploader.files_uploaded"], 5)
            self.failUnlessEqual(data["stats"]["chk_upload_helper.upload_need_upload"], 1)
        d.addCallback(_got_stats_json)

        # TODO: mangle the second segment of a file, to test errors that
        # occur after we've already sent some good data, which uses a
        # different error path.

        # TODO: download a URI with a form
        # TODO: create a directory by using a form
        # TODO: upload by using a form on the directory page
        #    url = base + "somedir/subdir1/freeform_post!!upload"
        # TODO: delete a file by using a button on the directory page

        return d

    @defer.inlineCallbacks
    def _test_runner(self, res):
        # exercise some of the diagnostic tools in runner.py

        # find a share
        for (dirpath, dirnames, filenames) in os.walk(unicode(self.basedir)):
            if "storage" not in dirpath:
                continue
            if not filenames:
                continue
            pieces = dirpath.split(os.sep)
            if (len(pieces) >= 4
                and pieces[-4] == "storage"
                and pieces[-3] == "shares"):
                # we're sitting in .../storage/shares/$START/$SINDEX , and there
                # are sharefiles here
                filename = os.path.join(dirpath, filenames[0])
                # peek at the magic to see if it is a chk share
                magic = open(filename, "rb").read(4)
                if magic == '\x00\x00\x00\x01':
                    break
        else:
            self.fail("unable to find any uri_extension files in %r"
                      % self.basedir)
        log.msg("test_system.SystemTest._test_runner using %r" % filename)

        rc,output,err = yield run_cli("debug", "dump-share", "--offsets",
                                      unicode_to_argv(filename))
        self.failUnlessEqual(rc, 0)

        # we only upload a single file, so we can assert some things about
        # its size and shares.
        self.failUnlessIn("share filename: %s" % quote_output(abspath_expanduser_unicode(filename)), output)
        self.failUnlessIn("size: %d\n" % len(self.data), output)
        self.failUnlessIn("num_segments: 1\n", output)
        # segment_size is always a multiple of needed_shares
        self.failUnlessIn("segment_size: %d\n" % mathutil.next_multiple(len(self.data), 3), output)
        self.failUnlessIn("total_shares: 10\n", output)
        # keys which are supposed to be present
        for key in ("size", "num_segments", "segment_size",
                    "needed_shares", "total_shares",
                    "codec_name", "codec_params", "tail_codec_params",
                    #"plaintext_hash", "plaintext_root_hash",
                    "crypttext_hash", "crypttext_root_hash",
                    "share_root_hash", "UEB_hash"):
            self.failUnlessIn("%s: " % key, output)
        self.failUnlessIn("  verify-cap: URI:CHK-Verifier:", output)

        # now use its storage index to find the other shares using the
        # 'find-shares' tool
        sharedir, shnum = os.path.split(filename)
        storagedir, storage_index_s = os.path.split(sharedir)
        storage_index_s = str(storage_index_s)
        nodedirs = [self.getdir("client%d" % i) for i in range(self.numclients)]
        rc,out,err = yield run_cli("debug", "find-shares", storage_index_s,
                                   *nodedirs)
        self.failUnlessEqual(rc, 0)
        sharefiles = [sfn.strip() for sfn in out.splitlines()]
        self.failUnlessEqual(len(sharefiles), 10)

        # also exercise the 'catalog-shares' tool
        nodedirs = [self.getdir("client%d" % i) for i in range(self.numclients)]
        rc,out,err = yield run_cli("debug", "catalog-shares", *nodedirs)
        self.failUnlessEqual(rc, 0)
        descriptions = [sfn.strip() for sfn in out.splitlines()]
        self.failUnlessEqual(len(descriptions), 30)
        matching = [line
                    for line in descriptions
                    if line.startswith("CHK %s " % storage_index_s)]
        self.failUnlessEqual(len(matching), 10)

    def _test_control(self, res):
        # exercise the remote-control-the-client foolscap interfaces in
        # allmydata.control (mostly used for performance tests)
        c0 = self.clients[0]
        control_furl_file = os.path.join(c0.basedir, "private", "control.furl")
        control_furl = open(control_furl_file, "r").read().strip()
        # it doesn't really matter which Tub we use to connect to the client,
        # so let's just use our IntroducerNode's
        d = self.introducer.tub.getReference(control_furl)
        d.addCallback(self._test_control2, control_furl_file)
        return d
    def _test_control2(self, rref, filename):
        d = defer.succeed(None)
        d.addCallback(lambda res: rref.callRemote("speed_test", 1, 200, False))
        if sys.platform in ("linux2", "linux3"):
            d.addCallback(lambda res: rref.callRemote("get_memory_usage"))
        d.addCallback(lambda res: rref.callRemote("measure_peer_response_time"))
        return d

    def _test_cli(self, res):
        # run various CLI commands (in a thread, since they use blocking
        # network calls)

        private_uri = self._private_node.get_uri()
        client0_basedir = self.getdir("client0")

        nodeargs = [
            "--node-directory", client0_basedir,
            ]

        d = defer.succeed(None)

        # for compatibility with earlier versions, private/root_dir.cap is
        # supposed to be treated as an alias named "tahoe:". Start by making
        # sure that works, before we add other aliases.

        root_file = os.path.join(client0_basedir, "private", "root_dir.cap")
        f = open(root_file, "w")
        f.write(private_uri)
        f.close()

        @defer.inlineCallbacks
        def run(ignored, verb, *args, **kwargs):
            rc,out,err = yield run_cli(verb, *args, nodeargs=nodeargs, **kwargs)
            defer.returnValue((out,err))

        def _check_ls((out,err), expected_children, unexpected_children=[]):
            self.failUnlessEqual(err, "")
            for s in expected_children:
                self.failUnless(s in out, (s,out))
            for s in unexpected_children:
                self.failIf(s in out, (s,out))

        def _check_ls_root((out,err)):
            self.failUnless("personal" in out)
            self.failUnless("s2-ro" in out)
            self.failUnless("s2-rw" in out)
            self.failUnlessEqual(err, "")

        # this should reference private_uri
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["personal", "s2-ro", "s2-rw"])

        d.addCallback(run, "list-aliases")
        def _check_aliases_1((out,err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(out.strip(" \n"), "tahoe: %s" % private_uri)
        d.addCallback(_check_aliases_1)

        # now that that's out of the way, remove root_dir.cap and work with
        # new files
        d.addCallback(lambda res: os.unlink(root_file))
        d.addCallback(run, "list-aliases")
        def _check_aliases_2((out,err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(out, "")
        d.addCallback(_check_aliases_2)

        d.addCallback(run, "mkdir")
        def _got_dir( (out,err) ):
            self.failUnless(uri.from_string_dirnode(out.strip()))
            return out.strip()
        d.addCallback(_got_dir)
        d.addCallback(lambda newcap: run(None, "add-alias", "tahoe", newcap))

        d.addCallback(run, "list-aliases")
        def _check_aliases_3((out,err)):
            self.failUnlessEqual(err, "")
            self.failUnless("tahoe: " in out)
        d.addCallback(_check_aliases_3)

        def _check_empty_dir((out,err)):
            self.failUnlessEqual(out, "")
            self.failUnlessEqual(err, "")
        d.addCallback(run, "ls")
        d.addCallback(_check_empty_dir)

        def _check_missing_dir((out,err)):
            # TODO: check that rc==2
            self.failUnlessEqual(out, "")
            self.failUnlessEqual(err, "No such file or directory\n")
        d.addCallback(run, "ls", "bogus")
        d.addCallback(_check_missing_dir)

        files = []
        datas = []
        for i in range(10):
            fn = os.path.join(self.basedir, "file%d" % i)
            files.append(fn)
            data = "data to be uploaded: file%d\n" % i
            datas.append(data)
            open(fn,"wb").write(data)

        def _check_stdout_against((out,err), filenum=None, data=None):
            self.failUnlessEqual(err, "")
            if filenum is not None:
                self.failUnlessEqual(out, datas[filenum])
            if data is not None:
                self.failUnlessEqual(out, data)

        # test all both forms of put: from a file, and from stdin
        #  tahoe put bar FOO
        d.addCallback(run, "put", files[0], "tahoe-file0")
        def _put_out((out,err)):
            self.failUnless("URI:LIT:" in out, out)
            self.failUnless("201 Created" in err, err)
            uri0 = out.strip()
            return run(None, "get", uri0)
        d.addCallback(_put_out)
        d.addCallback(lambda (out,err): self.failUnlessEqual(out, datas[0]))

        d.addCallback(run, "put", files[1], "subdir/tahoe-file1")
        #  tahoe put bar tahoe:FOO
        d.addCallback(run, "put", files[2], "tahoe:file2")
        d.addCallback(run, "put", "--format=SDMF", files[3], "tahoe:file3")
        def _check_put_mutable((out,err)):
            self._mutable_file3_uri = out.strip()
        d.addCallback(_check_put_mutable)
        d.addCallback(run, "get", "tahoe:file3")
        d.addCallback(_check_stdout_against, 3)

        #  tahoe put FOO
        STDIN_DATA = "This is the file to upload from stdin."
        d.addCallback(run, "put", "-", "tahoe-file-stdin", stdin=STDIN_DATA)
        #  tahoe put tahoe:FOO
        d.addCallback(run, "put", "-", "tahoe:from-stdin",
                      stdin="Other file from stdin.")

        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["tahoe-file0", "file2", "file3", "subdir",
                                  "tahoe-file-stdin", "from-stdin"])
        d.addCallback(run, "ls", "subdir")
        d.addCallback(_check_ls, ["tahoe-file1"])

        # tahoe mkdir FOO
        d.addCallback(run, "mkdir", "subdir2")
        d.addCallback(run, "ls")
        # TODO: extract the URI, set an alias with it
        d.addCallback(_check_ls, ["subdir2"])

        # tahoe get: (to stdin and to a file)
        d.addCallback(run, "get", "tahoe-file0")
        d.addCallback(_check_stdout_against, 0)
        d.addCallback(run, "get", "tahoe:subdir/tahoe-file1")
        d.addCallback(_check_stdout_against, 1)
        outfile0 = os.path.join(self.basedir, "outfile0")
        d.addCallback(run, "get", "file2", outfile0)
        def _check_outfile0((out,err)):
            data = open(outfile0,"rb").read()
            self.failUnlessEqual(data, "data to be uploaded: file2\n")
        d.addCallback(_check_outfile0)
        outfile1 = os.path.join(self.basedir, "outfile0")
        d.addCallback(run, "get", "tahoe:subdir/tahoe-file1", outfile1)
        def _check_outfile1((out,err)):
            data = open(outfile1,"rb").read()
            self.failUnlessEqual(data, "data to be uploaded: file1\n")
        d.addCallback(_check_outfile1)

        d.addCallback(run, "rm", "tahoe-file0")
        d.addCallback(run, "rm", "tahoe:file2")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, [], ["tahoe-file0", "file2"])

        d.addCallback(run, "ls", "-l")
        def _check_ls_l((out,err)):
            lines = out.split("\n")
            for l in lines:
                if "tahoe-file-stdin" in l:
                    self.failUnless(l.startswith("-r-- "), l)
                    self.failUnless(" %d " % len(STDIN_DATA) in l)
                if "file3" in l:
                    self.failUnless(l.startswith("-rw- "), l) # mutable
        d.addCallback(_check_ls_l)

        d.addCallback(run, "ls", "--uri")
        def _check_ls_uri((out,err)):
            lines = out.split("\n")
            for l in lines:
                if "file3" in l:
                    self.failUnless(self._mutable_file3_uri in l)
        d.addCallback(_check_ls_uri)

        d.addCallback(run, "ls", "--readonly-uri")
        def _check_ls_rouri((out,err)):
            lines = out.split("\n")
            for l in lines:
                if "file3" in l:
                    rw_uri = self._mutable_file3_uri
                    u = uri.from_string_mutable_filenode(rw_uri)
                    ro_uri = u.get_readonly().to_string()
                    self.failUnless(ro_uri in l)
        d.addCallback(_check_ls_rouri)


        d.addCallback(run, "mv", "tahoe-file-stdin", "tahoe-moved")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["tahoe-moved"], ["tahoe-file-stdin"])

        d.addCallback(run, "ln", "tahoe-moved", "newlink")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["tahoe-moved", "newlink"])

        d.addCallback(run, "cp", "tahoe:file3", "tahoe:file3-copy")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["file3", "file3-copy"])
        d.addCallback(run, "get", "tahoe:file3-copy")
        d.addCallback(_check_stdout_against, 3)

        # copy from disk into tahoe
        d.addCallback(run, "cp", files[4], "tahoe:file4")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["file3", "file3-copy", "file4"])
        d.addCallback(run, "get", "tahoe:file4")
        d.addCallback(_check_stdout_against, 4)

        # copy from tahoe into disk
        target_filename = os.path.join(self.basedir, "file-out")
        d.addCallback(run, "cp", "tahoe:file4", target_filename)
        def _check_cp_out((out,err)):
            self.failUnless(os.path.exists(target_filename))
            got = open(target_filename,"rb").read()
            self.failUnlessEqual(got, datas[4])
        d.addCallback(_check_cp_out)

        # copy from disk to disk (silly case)
        target2_filename = os.path.join(self.basedir, "file-out-copy")
        d.addCallback(run, "cp", target_filename, target2_filename)
        def _check_cp_out2((out,err)):
            self.failUnless(os.path.exists(target2_filename))
            got = open(target2_filename,"rb").read()
            self.failUnlessEqual(got, datas[4])
        d.addCallback(_check_cp_out2)

        # copy from tahoe into disk, overwriting an existing file
        d.addCallback(run, "cp", "tahoe:file3", target_filename)
        def _check_cp_out3((out,err)):
            self.failUnless(os.path.exists(target_filename))
            got = open(target_filename,"rb").read()
            self.failUnlessEqual(got, datas[3])
        d.addCallback(_check_cp_out3)

        # copy from disk into tahoe, overwriting an existing immutable file
        d.addCallback(run, "cp", files[5], "tahoe:file4")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["file3", "file3-copy", "file4"])
        d.addCallback(run, "get", "tahoe:file4")
        d.addCallback(_check_stdout_against, 5)

        # copy from disk into tahoe, overwriting an existing mutable file
        d.addCallback(run, "cp", files[5], "tahoe:file3")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["file3", "file3-copy", "file4"])
        d.addCallback(run, "get", "tahoe:file3")
        d.addCallback(_check_stdout_against, 5)

        # recursive copy: setup
        dn = os.path.join(self.basedir, "dir1")
        os.makedirs(dn)
        open(os.path.join(dn, "rfile1"), "wb").write("rfile1")
        open(os.path.join(dn, "rfile2"), "wb").write("rfile2")
        open(os.path.join(dn, "rfile3"), "wb").write("rfile3")
        sdn2 = os.path.join(dn, "subdir2")
        os.makedirs(sdn2)
        open(os.path.join(sdn2, "rfile4"), "wb").write("rfile4")
        open(os.path.join(sdn2, "rfile5"), "wb").write("rfile5")

        # from disk into tahoe
        d.addCallback(run, "cp", "-r", dn, "tahoe:")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["dir1"])
        d.addCallback(run, "ls", "dir1")
        d.addCallback(_check_ls, ["rfile1", "rfile2", "rfile3", "subdir2"],
                      ["rfile4", "rfile5"])
        d.addCallback(run, "ls", "tahoe:dir1/subdir2")
        d.addCallback(_check_ls, ["rfile4", "rfile5"],
                      ["rfile1", "rfile2", "rfile3"])
        d.addCallback(run, "get", "dir1/subdir2/rfile4")
        d.addCallback(_check_stdout_against, data="rfile4")

        # and back out again
        dn_copy = os.path.join(self.basedir, "dir1-copy")
        d.addCallback(run, "cp", "--verbose", "-r", "tahoe:dir1", dn_copy)
        def _check_cp_r_out((out,err)):
            def _cmp(name):
                old = open(os.path.join(dn, name), "rb").read()
                newfn = os.path.join(dn_copy, "dir1", name)
                self.failUnless(os.path.exists(newfn))
                new = open(newfn, "rb").read()
                self.failUnlessEqual(old, new)
            _cmp("rfile1")
            _cmp("rfile2")
            _cmp("rfile3")
            _cmp(os.path.join("subdir2", "rfile4"))
            _cmp(os.path.join("subdir2", "rfile5"))
        d.addCallback(_check_cp_r_out)

        # and copy it a second time, which ought to overwrite the same files
        d.addCallback(run, "cp", "-r", "tahoe:dir1", dn_copy)

        # and again, only writing filecaps
        dn_copy2 = os.path.join(self.basedir, "dir1-copy-capsonly")
        d.addCallback(run, "cp", "-r", "--caps-only", "tahoe:dir1", dn_copy2)
        def _check_capsonly((out,err)):
            # these should all be LITs
            x = open(os.path.join(dn_copy2, "dir1", "subdir2", "rfile4")).read()
            y = uri.from_string_filenode(x)
            self.failUnlessEqual(y.data, "rfile4")
        d.addCallback(_check_capsonly)

        # and tahoe-to-tahoe
        d.addCallback(run, "cp", "-r", "tahoe:dir1", "tahoe:dir1-copy")
        d.addCallback(run, "ls")
        d.addCallback(_check_ls, ["dir1", "dir1-copy"])
        d.addCallback(run, "ls", "dir1-copy/dir1")
        d.addCallback(_check_ls, ["rfile1", "rfile2", "rfile3", "subdir2"],
                      ["rfile4", "rfile5"])
        d.addCallback(run, "ls", "tahoe:dir1-copy/dir1/subdir2")
        d.addCallback(_check_ls, ["rfile4", "rfile5"],
                      ["rfile1", "rfile2", "rfile3"])
        d.addCallback(run, "get", "dir1-copy/dir1/subdir2/rfile4")
        d.addCallback(_check_stdout_against, data="rfile4")

        # and copy it a second time, which ought to overwrite the same files
        d.addCallback(run, "cp", "-r", "tahoe:dir1", "tahoe:dir1-copy")

        # tahoe_ls doesn't currently handle the error correctly: it tries to
        # JSON-parse a traceback.
##         def _ls_missing(res):
##             argv = nodeargs + ["ls", "bogus"]
##             return self._run_cli(argv)
##         d.addCallback(_ls_missing)
##         def _check_ls_missing((out,err)):
##             print "OUT", out
##             print "ERR", err
##             self.failUnlessEqual(err, "")
##         d.addCallback(_check_ls_missing)

        return d

    def test_filesystem_with_cli_in_subprocess(self):
        # We do this in a separate test so that test_filesystem doesn't skip if we can't run bin/tahoe.

        self.basedir = "system/SystemTest/test_filesystem_with_cli_in_subprocess"
        d = self.set_up_nodes()
        def _new_happy_semantics(ign):
            for c in self.clients:
                c.encoding_params['happy'] = 1
        d.addCallback(_new_happy_semantics)

        def _run_in_subprocess(ignored, verb, *args, **kwargs):
            stdin = kwargs.get("stdin")
            env = kwargs.get("env")
            newargs = ["--node-directory", self.getdir("client0"), verb] + list(args)
            return self.run_bintahoe(newargs, stdin=stdin, env=env)

        def _check_succeeded(res, check_stderr=True):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 0, str(res))
            if check_stderr:
                self.failUnlessEqual(err, "")

        d.addCallback(_run_in_subprocess, "create-alias", "newalias")
        d.addCallback(_check_succeeded)

        STDIN_DATA = "This is the file to upload from stdin."
        d.addCallback(_run_in_subprocess, "put", "-", "newalias:tahoe-file", stdin=STDIN_DATA)
        d.addCallback(_check_succeeded, check_stderr=False)

        def _mv_with_http_proxy(ign):
            env = os.environ
            env['http_proxy'] = env['HTTP_PROXY'] = "http://127.0.0.0:12345"  # invalid address
            return _run_in_subprocess(None, "mv", "newalias:tahoe-file", "newalias:tahoe-moved", env=env)
        d.addCallback(_mv_with_http_proxy)
        d.addCallback(_check_succeeded)

        d.addCallback(_run_in_subprocess, "ls", "newalias:")
        def _check_ls(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 0, str(res))
            self.failUnlessEqual(err, "", str(res))
            self.failUnlessIn("tahoe-moved", out)
            self.failIfIn("tahoe-file", out)
        d.addCallback(_check_ls)
        return d

    def _test_checker(self, res):
        ut = upload.Data("too big to be literal" * 200, convergence=None)
        d = self._personal_node.add_file(u"big file", ut)

        d.addCallback(lambda res: self._personal_node.check(Monitor()))
        def _check_dirnode_results(r):
            self.failUnless(r.is_healthy())
        d.addCallback(_check_dirnode_results)
        d.addCallback(lambda res: self._personal_node.check(Monitor(), verify=True))
        d.addCallback(_check_dirnode_results)

        d.addCallback(lambda res: self._personal_node.get(u"big file"))
        def _got_chk_filenode(n):
            self.failUnless(isinstance(n, ImmutableFileNode))
            d = n.check(Monitor())
            def _check_filenode_results(r):
                self.failUnless(r.is_healthy())
            d.addCallback(_check_filenode_results)
            d.addCallback(lambda res: n.check(Monitor(), verify=True))
            d.addCallback(_check_filenode_results)
            return d
        d.addCallback(_got_chk_filenode)

        d.addCallback(lambda res: self._personal_node.get(u"sekrit data"))
        def _got_lit_filenode(n):
            self.failUnless(isinstance(n, LiteralFileNode))
            d = n.check(Monitor())
            def _check_lit_filenode_results(r):
                self.failUnlessEqual(r, None)
            d.addCallback(_check_lit_filenode_results)
            d.addCallback(lambda res: n.check(Monitor(), verify=True))
            d.addCallback(_check_lit_filenode_results)
            return d
        d.addCallback(_got_lit_filenode)
        return d


class Connections(SystemTestMixin, unittest.TestCase):
    def test_rref(self):
        self.basedir = "system/Connections/rref"
        d = self.set_up_nodes(2)
        def _start(ign):
            self.c0 = self.clients[0]
            nonclients = [s for s in self.c0.storage_broker.get_connected_servers()
                          if s.get_serverid() != self.c0.get_long_nodeid()]
            self.failUnlessEqual(len(nonclients), 1)

            self.s1 = nonclients[0]  # s1 is the server, not c0
            self.s1_rref = self.s1.get_rref()
            self.failIfEqual(self.s1_rref, None)
            self.failUnless(self.s1.is_connected())
        d.addCallback(_start)

        # now shut down the server
        d.addCallback(lambda ign: self.clients[1].disownServiceParent())
        # and wait for the client to notice
        def _poll():
            return len(self.c0.storage_broker.get_connected_servers()) < 2
        d.addCallback(lambda ign: self.poll(_poll))

        def _down(ign):
            self.failIf(self.s1.is_connected())
            rref = self.s1.get_rref()
            self.failUnless(rref)
            self.failUnlessIdentical(rref, self.s1_rref)
        d.addCallback(_down)
        return d
