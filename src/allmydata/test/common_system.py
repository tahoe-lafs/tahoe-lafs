"""
Test infrastructure for integration-y tests that run actual nodes, like those
in ``allmydata.test.test_system``.

Ported to Python 3.
"""

from typing import Optional
import os
from functools import partial

from twisted.internet import reactor
from twisted.internet import defer
from twisted.internet.defer import inlineCallbacks
from twisted.internet.task import deferLater
from twisted.application import service

from foolscap.api import flushEventualQueue

from allmydata import client
from allmydata.introducer.server import create_introducer
from allmydata.util import fileutil, log, pollmixin
from allmydata.util.deferredutil import async_to_deferred
from allmydata.storage import http_client
from allmydata.storage_client import (
    NativeStorageServer,
    HTTPNativeStorageServer,
)

from twisted.python.filepath import (
    FilePath,
)

from .common import (
    SameProcessStreamEndpointAssigner,
)

from . import common_util as testutil
from ..scripts.common import (
    write_introducer,
)

# our system test uses the same Tub certificates each time, to avoid the
# overhead of key generation
SYSTEM_TEST_CERTS = [
"""-----BEGIN CERTIFICATE-----
MIICojCCAYoCAQEwDQYJKoZIhvcNAQELBQAwFzEVMBMGA1UEAwwMbmV3cGJfdGhp
bmd5MB4XDTIwMDEwMjAxNDAzM1oXDTIxMDEwMTAxNDAzM1owFzEVMBMGA1UEAwwM
bmV3cGJfdGhpbmd5MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA1iNV
z07PYwZwucl87QlL2TFZvDxD4flZ/p3BZE3DCT5Efn9w2NT4sHXL1e+R/qsDFuNG
bw1y1TRM0DGK6Wr0XRT2mLQULNgB8y/HrhcSdONsYRyWdj+LimyECKjwh0iSkApv
Yj/7IOuq6dOoh67YXPdf75OHLShm4+8q8fuwhBL+nuuO4NhZDJKupYHcnuCkcF88
LN77HKrrgbpyVmeghUkwJMLeJCewvYVlambgWRiuGGexFgAm6laS3rWetOcdm9eg
FoA9PKNN6xvPatbj99MPoLpBbzsI64M0yT/wTSw1pj/Nom3rwfMa2OH8Kk7c8R/r
U3xj4ZY1DTlGERvejQIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQAwyQjQ3ZgtJ3JW
r3/EPdqSUBamTfXIpOh9rXmRjPpbe+MvenqIzl4q+GnkL5mdEb1e1hdKQZgFQ5Q5
tbcNIz6h5C07KaNtbqhZCx5c/RUEH87VeXuAuOqZHbZWJ18q0tnk+YgWER2TOkgE
RI2AslcsJBt88UUOjHX6/7J3KjPFaAjW1QV3TTsHxk14aYDYJwPdz+ijchgbOPQ0
i+ilhzcB+qQnOC1s4xQSFo+zblTO7EgqM9KpupYfOVFh46P1Mak2W8EDvhz0livl
OROXJ6nR/13lmQdfVX6T45d+ITBwtmW2nGAh3oI3JlArGKHaW+7qnuHR72q9FSES
cEYA/wmk
-----END CERTIFICATE-----
-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDWI1XPTs9jBnC5
yXztCUvZMVm8PEPh+Vn+ncFkTcMJPkR+f3DY1PiwdcvV75H+qwMW40ZvDXLVNEzQ
MYrpavRdFPaYtBQs2AHzL8euFxJ042xhHJZ2P4uKbIQIqPCHSJKQCm9iP/sg66rp
06iHrthc91/vk4ctKGbj7yrx+7CEEv6e647g2FkMkq6lgdye4KRwXzws3vscquuB
unJWZ6CFSTAkwt4kJ7C9hWVqZuBZGK4YZ7EWACbqVpLetZ605x2b16AWgD08o03r
G89q1uP30w+gukFvOwjrgzTJP/BNLDWmP82ibevB8xrY4fwqTtzxH+tTfGPhljUN
OUYRG96NAgMBAAECggEAJ5xztBx0+nFnisZ9yG8uy6d4XPyc5gE1J4dRDdfgmyYc
j3XNjx6ePi4cHZ/qVryVnrc+AS7wrgW1q9FuS81QFKPbFdZB4SW3/p85BbgY3uxu
0Ovz3T3V9y4polx12eCP0/tKLVd+gdF2VTik9Sxfs5rC8VNN7wmJNuK4A/k15sgy
BIu/R8NlMNGQySNhtccp+dzB8uTyKx5zFZhVvnAK/3YX9BC2V4QBW9JxO4S8N0/9
48e9Sw/fGCfQ/EFPKGCvTvfuRqJ+4t5k10FygXJ+s+y70ifYi+aSsjJBuranbLJp
g5TwhuKnTWs8Nth3YRLbcJL4VBIOehjAWy8pDMMtlQKBgQD0O8cHb8cOTGW0BijC
NDofhA2GooQUUR3WL324PXWZq0DXuBDQhJVBKWO3AYonivhhd/qWO8lea9MEmU41
nKZ7maS4B8AJLJC08P8GL1uCIE/ezEXEi9JwC1zJiyl595Ap4lSAozH0DwjNvmGL
5mIdYg0BliqFXbloNJkNlb7INwKBgQDgdGEIWXc5Y1ncWNs6iDIV/t2MlL8vLrP0
hpkl/QiMndOQyD6JBo0+ZqvOQTSS4NTSxBROjPxvFbEJ3eH8Pmn8gHOf46fzP1OJ
wlYv0gYzkN4FE/tN6JnO2u9pN0euyyZLM1fnEcrMWColMN8JlWjtA7Gbxm8lkfa4
3vicaJtlWwKBgQCQYL4ZgVR0+Wit8W4qz+EEPHYafvwBXqp6sXxqa7qXawtb+q3F
9nqdGLCfwMNA+QA37ksugI1byfXmpBH902r/aiZbvAkj4zpwHH9F0r0PwbY1iSA9
PkLahX0Gj8OnHFgWynsVyGOBWVnk9oSHxVt+7zWtGG5uhKdUGLPZugocJQKBgB61
7bzduOFiRZ5PjhdxISE/UQL2Kz6Cbl7rt7Kp72yF/7eUnnHTMqoyFBnRdCcQmi4I
ZBrnUXbFigamlFAWHhxNWwSqeoVeychUjcRXQT/291nMhRsA02KpNA66YJV6+E9b
xBA6r/vLqGCUUkAWcFfVpIyC1xxV32MmJvAHpBN3AoGAPF3MUFiO0iKNZfst6Tm3
rzrldLawDo98DRZ7Yb2kWlWZYqUk/Nvryvo2cns75WGSMDYVbbRp+BY7kZmNYa9K
iQzKDL54ZRu6V+getJdeAO8yXoCmnZKxt5OHvOSrQMfAmFKSwLwxBbZBfXEyuune
yfusXLtCgajpreoVIa0xWdQ=
-----END PRIVATE KEY-----
""", # 0
"""-----BEGIN CERTIFICATE-----
MIICojCCAYoCAQEwDQYJKoZIhvcNAQELBQAwFzEVMBMGA1UEAwwMbmV3cGJfdGhp
bmd5MB4XDTIwMDEwMjAxNDAzM1oXDTIxMDEwMTAxNDAzM1owFzEVMBMGA1UEAwwM
bmV3cGJfdGhpbmd5MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEApDzW
4ZBeK9w4xpRaed6lXzeCO0Xmr3f0ynbueSdiZ89FWoAMgK+SiBIOViYV6hfm0Wah
lemSNzFGx5LvDSg2uwSqEP23DeM9O/SQPgIAiLeeEsYZJcgg2jz92YfFEaahsGdI
6qSP4XI2/5dgKRpPOYDGyw6R5PQR6w22Xq1WD1jBvImk/k09I9jHRn40pYbaJzbg
U2aIjvOruo2kqe4f6iDqE0piYimAZJUvemu1UoyV5NG590hGkDuWsMD77+d2FxCj
9Nzb+iuuG3ksnanHPyXi1hQmzp5OmzVWaevCHinNjWgsuSuLGO9H2SLf3wwp2UCs
EpKtzoKrnZdEg/anNwIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQChxtr67o1aZZMJ
A6gESPtFjZLw6wG0j50JsrWKLvoXVts1ToJ9u2nx01aFKjBwb4Yg+vdJfDgIIAEm
jS56h6H2DfJlkTWHmi8Vx1wuusWnrNwYMI53tdlRIpD2+Ne7yeoLQZcVN2wuPmxD
Mbksg4AI4csmbkU/NPX5DtMy4EzM/pFvIcxNIVRUMVTFzn5zxhKfhyPqrMI4fxw1
UhUbEKO+QgIqTNp/dZ0lTbFs5HJQn6yirWyyvQKBPmaaK+pKd0RST/T38OU2oJ/J
LojRs7ugCJ+bxJqegmQrdcVqZZGbpYeK4O/5eIn8KOlgh0nUza1MyjJJemgBBWf7
HoXB8Fge
-----END CERTIFICATE-----
-----BEGIN PRIVATE KEY-----
MIIEvwIBADANBgkqhkiG9w0BAQEFAASCBKkwggSlAgEAAoIBAQCkPNbhkF4r3DjG
lFp53qVfN4I7Reavd/TKdu55J2Jnz0VagAyAr5KIEg5WJhXqF+bRZqGV6ZI3MUbH
ku8NKDa7BKoQ/bcN4z079JA+AgCIt54SxhklyCDaPP3Zh8URpqGwZ0jqpI/hcjb/
l2ApGk85gMbLDpHk9BHrDbZerVYPWMG8iaT+TT0j2MdGfjSlhtonNuBTZoiO86u6
jaSp7h/qIOoTSmJiKYBklS96a7VSjJXk0bn3SEaQO5awwPvv53YXEKP03Nv6K64b
eSydqcc/JeLWFCbOnk6bNVZp68IeKc2NaCy5K4sY70fZIt/fDCnZQKwSkq3Ogqud
l0SD9qc3AgMBAAECggEBAIu55uaIOFYASZ1IYaEFNpRHWVisI5Js76nAfSo9w46l
3E8eWYSx2mxBUEkipco/A3RraFVuHaMvHRR1gUMkT0vUsAs8jxwVk+cKLh1S/rlR
3f4C4yotlSWWdjE3PQXDShQWCwb1ciNPVFMmqfzOEVDOqlHe12h97TCYverWdT0f
3LZICLQsZd1WPKnPNXrsRRDCBuRLapdg+M0oJ+y6IiCdm+qM7Qvaoef6hlvm5ECz
LCM92db5BKTuPOQXMx2J8mjaBgU3aHxRV08IFgs7mI6q0t0FM7LlytIAJq1Hg5QU
36zDKo8tblkPijWZWlqlZCnlarrd3Ar/BiLEiuOGDMECgYEA1GOp0KHy0mbJeN13
+TDsgP7zhmqFcuJREu2xziNJPK2S06NfGYE8vuVqBGzBroLTQ3dK7rOJs9C6IjCE
mH7ZeHzfcKohpZnl443vHMSpgdh/bXTEO1aQZNbJ2hLYs8ie/VqqHR0u6YtpUqZL
LgaUA0U8GnlsO55B8kyCelckmDkCgYEAxfYQMPEEzg1tg2neqEfyoeY0qQTEJTeh
CPMztowSJpIyF1rQH6TaG0ZchkiAkw3W58RVDfvK72TuVlC5Kz00C2/uPnrqm0dX
iMPeML5rFlG3VGCrSTnAPI+az6P65q8zodqcTtA8xoxgPOlc/lINOxiTEMxLyeGF
8GyP+sCM2u8CgYEAvMBR05OJnEky9hJEpBZBqSZrQGL8dCwDh0HtCdi8JovPd/yx
8JW1aaWywXnx6uhjXoru8hJm54IxWV8rB+d716OKY7MfMfACqWejQDratgW0wY7L
MjztGGD2hLLJGYXLHjfsBPHBllaKZKRbHe1Er19hWdndQWKVEwPB1X4KjKkCgYEA
nWHmN3K2djbYtRyLR1CEBtDlVuaSJmCWp23q1BuCJqYeKtEpG69NM1f6IUws5Dyh
eXtuf4KKMU8V6QueW1D6OomPaJ8CO9c5MWM/F5ObwY/P58Y/ByVhvwQQeToONC5g
JzKNCF+nodZigKqrIwoKuMvtx/IT4vloKd+1jA5fLYMCgYBoT3HLCyATVdDSt1TZ
SbEDoLSYt23KRjQV93+INP949dYCagtgh/kTzxBopw5FljISLfdYizIRo2AzhhfP
WWpILlnt19kD+sNirJVqxJacfEZsu5baWTedI/yrCuVsAs/s3/EEY6q0Qywknxtp
Fwh1/8y5t14ib5fxOVhi8X1nEA==
-----END PRIVATE KEY-----
""", # 1
"""-----BEGIN CERTIFICATE-----
MIICojCCAYoCAQEwDQYJKoZIhvcNAQELBQAwFzEVMBMGA1UEAwwMbmV3cGJfdGhp
bmd5MB4XDTIwMDEwMjAxNDAzM1oXDTIxMDEwMTAxNDAzM1owFzEVMBMGA1UEAwwM
bmV3cGJfdGhpbmd5MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwMTn
hXnpKHGAir3WYbOxefVrMA07OZNAsNa29nBwLA+NVIJNUFgquibMj7QYo8+M45oY
6LKr4yRcBryZVvyxfdr92xp8+kLeVApk2WLjkdBTRagHh9qdrY0hQmagCBN6/hLG
Xug8VksQUdhX3vu6ZyMvTLfKRkDOMRVkRGRGg/dOcvom7zpqMCGYenMG2FStr6UV
3s3dlCSZZTdTX5Uoq6yfUUJE3nITGKjpnpJKqIs3PWCIxdj7INIcjJKvIdUcavIV
2hEhh60A8ltmtdpQAXVBE+U7aZgS1fGAWS2A0a3UwuP2pkQp6OyKCUVHpZatbl9F
ahDN2QBzegv/rdJ1zwIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQAl4OQZ+FB9ZSUv
FL/KwLNt+ONU8Sve/xiX+8vKAvgKm2FrjyK+AZPwibnu+FSt2G4ndZBx4Wvpe5V+
gCsbzSXlh9cDn2SRXyprt2l/8Fj4eUMaThmLKOK200/N/s2SpmBtnuflBrhNaJpw
DEi2KEPuXsgvkuVzXN06j75cUHwn5LeWDAh0RalkVuGbEWBoFx9Hq8WECdlCy0YS
y09+yO01qz70y88C2rPThKw8kP4bX8aFZbvsnRHsLu/8nEQNlrELcfBarPVHjJ/9
imxOdymJkV152V58voiXP/PwXhynctQbF7e+0UZ+XEGdbAbZA0BMl7z+b09Z+jF2
afm4mVox
-----END CERTIFICATE-----
-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDAxOeFeekocYCK
vdZhs7F59WswDTs5k0Cw1rb2cHAsD41Ugk1QWCq6JsyPtBijz4zjmhjosqvjJFwG
vJlW/LF92v3bGnz6Qt5UCmTZYuOR0FNFqAeH2p2tjSFCZqAIE3r+EsZe6DxWSxBR
2Ffe+7pnIy9Mt8pGQM4xFWREZEaD905y+ibvOmowIZh6cwbYVK2vpRXezd2UJJll
N1NflSirrJ9RQkTechMYqOmekkqoizc9YIjF2Psg0hyMkq8h1Rxq8hXaESGHrQDy
W2a12lABdUET5TtpmBLV8YBZLYDRrdTC4/amRCno7IoJRUellq1uX0VqEM3ZAHN6
C/+t0nXPAgMBAAECggEAF+2ZK4lZdsq4AQDVhqUuh4v+NSW/T0NHCWxto6OLWPzJ
N09BV5LKIvdD9yaM1HCj9XCgXOooyfYuciuhARo20f+H+VWNY+c+/8GWiSFsTCJG
4+Oao7NwVSWqljp07Ou2Hamo9AjxzGhe6znmlmg62CiW63f45MWQkqksHA0yb5jg
/onJ2//I+OI+aTKNfjt1G6h2x7oxeGTU1jJ0Hb2xSh+Mpqx9NDfb/KZyOndhSG5N
xRVosQ6uV+9mqHxTTwTZurTG31uhZzarkMuqxhcHS94ub7berEc/OlqvbyMKNZ3A
lzuvq0NBZhEUhAVgORAIS17r/q2BvyG4u5LFbG2p0QKBgQDeyyOl+A7xc4lPE2OL
Z3KHJPP4RuUnHnWFC+bNdr5Ag8K7jcjZIcasyUom9rOR0Fpuw9wmXpp3+6fyp9bJ
y6Bi5VioR0ZFP5X+nXxIN3yvgypu6AZvkhHrEFer+heGHxPlbwNKCKMbPzDZPBTZ
vlC7g7xUUcpNmGhrOKr3Qq5FlwKBgQDdgCmRvsHUyzicn8TI3IJBAOcaQG0Yr/R2
FzBqNfHHx7fUZlJfKJsnu9R9VRZmBi4B7MA2xcvz4QrdZWEtY8uoYp8TAGILfW1u
CP4ZHrzfDo/67Uzk2uTMTd0+JOqSm/HiVNguRPvC8EWBoFls+h129GKThMvKR1hP
1oarfAGIiQKBgQCIMAq5gHm59JMhqEt4QqMKo3cS9FtNX1wdGRpbzFMd4q0dstzs
ha4Jnv3Z9YHtBzzQap9fQQMRht6yARDVx8hhy6o3K2J0IBtTSfdXubtZGkfNBb4x
Y0vaseG1uam5jbO+0u5iygbSN/1nPUfNln2JMkzkCh8s8ZYavMgdX0BiPwKBgChR
QL/Hog5yoy5XIoGRKaBdYrNzkKgStwObuvNKOGUt5DckHNA3Wu6DkOzzRO1zKIKv
LlmJ7VLJ3qln36VcaeCPevcBddczkGyb9GxsHOLZCroY4YsykLzjW2cJXy0qd3/E
A8mAQvc7ttsebciZSi2x1BOX82QxUlDN8ptaKglJAoGBAMnLN1TQB0xtWYDPGcGV
2IvgX7OTRRlMVrTvIOvP5Julux9z1r0x0cesl/jaXupsBUlLLicPyBMSBJrXlr24
mrgkodk4TdqO1VtBCZBqak97DHVezstMrbpCGlUD5jBnsHVRLERvS09QlGhqMeNL
jpNQbWH9VhutzbvpYquKrhvK
-----END PRIVATE KEY-----
""", # 2
"""-----BEGIN CERTIFICATE-----
MIICojCCAYoCAQEwDQYJKoZIhvcNAQELBQAwFzEVMBMGA1UEAwwMbmV3cGJfdGhp
bmd5MB4XDTIwMDEwMjAxNDAzM1oXDTIxMDEwMTAxNDAzM1owFzEVMBMGA1UEAwwM
bmV3cGJfdGhpbmd5MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAypqi
YTni3s60Uo8vgGcFvjWWkB5CD9Fx9pW/2KcxRJ/u137Y+BG8qWMA4lgII3ZIuvo4
6rLDiXnAnDZqUtrvZ90O/gH6RyQqX3AI4EwPvCnRIIe0okRcxnxYBL/LfBY54xuv
46JRYZP4c9IImqQH9QVo2/egtEzcpbmT/mfhpf6NGQWC3Xps2BqDT2SV/DrX/wPA
8P1atE1AxNp8ENxK/cjFAteEyDZOsDSa757ZHKAdM7L8rZ1Fd2xAA1Dq7IyYpTNE
IX72xytWxllcNvSUPLT+oicsSZBadc/p3moc3tR/rNdgrHKybedadru/f9Gwpa+v
0sllZlEcVPSYddAzWwIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQCmk60Nj5FPvemx
DSSQjJPyJoIDpTxQ4luSzIq4hPwlUXw7dqrvHyCWgn2YVe9xZsGrT/+n376ecmgu
sw4s4qVhR9bzKkTMewjC2wUooTA5v9HYsNWZy3Ah7hHPbDHlMADYobjB5/XolNUP
bCM9xALEdM9DxpC4vjUZexlRKmjww9QKE22jIM+bqsK0zqDSq+zHpfHNGGcS3vva
OvI6FPc1fAr3pZpVzevMSN2zufIJwjL4FT5/uzwOCaSCwgR1ztD5CSbQLTLlwIsX
S7h2WF9078XumeRjKejdjEjyH4abKRq8+5LVLcjKEpg7OvktuRpPoGPCEToaAzuv
h+RSQwwY
-----END CERTIFICATE-----
-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDKmqJhOeLezrRS
jy+AZwW+NZaQHkIP0XH2lb/YpzFEn+7Xftj4EbypYwDiWAgjdki6+jjqssOJecCc
NmpS2u9n3Q7+AfpHJCpfcAjgTA+8KdEgh7SiRFzGfFgEv8t8FjnjG6/jolFhk/hz
0giapAf1BWjb96C0TNyluZP+Z+Gl/o0ZBYLdemzYGoNPZJX8Otf/A8Dw/Vq0TUDE
2nwQ3Er9yMUC14TINk6wNJrvntkcoB0zsvytnUV3bEADUOrsjJilM0QhfvbHK1bG
WVw29JQ8tP6iJyxJkFp1z+neahze1H+s12CscrJt51p2u79/0bClr6/SyWVmURxU
9Jh10DNbAgMBAAECggEBALv7Q+Rf+C7wrQDZF6LUc9CrGfq4CGVy2IGJKgqT/jOF
DO9nI1rv4hNr55sbQNneWtcZaYvht2mrzNlj57zepDjDM7DcFuLBHIuWgLXT/NmC
FyZOo3vXYBlNr8EgT2XfnXAp9UWJCmc2CtUzsIYC4dsmXMeTd8kyc5tUl4r5ybTf
1g+RTck/IGgqdfzpuTsNl79FW2rP9z111Py6dbqgQzhuSAune9dnLFvZst8dyL8j
FStETMxBM6jrCF1UcKXzG7trDHiCdzJ8WUhx6opN/8OasQGndwpXto6FZuBy/AVP
4kVQNpUXImYcLEpva0MqGRHg+YN+c84C71CMchnF4aECgYEA7J2go4CkCcZNKCy5
R5XVCqNFYRHjekR+UwH8cnCa7pMKKfP+lTCiBrO2q8zwWwknRMyuycS5g/xbSpg1
L6hi92CV1YQy1/JhlQedekjejNTTuLOPKf78AFNSfc7axDnes2v4Bvcdp9gsbUIO
10cXh0tOSLE7P9y+yC86KQkFAPECgYEA2zO0M2nvbPHv2jjtymY3pflYm0HzhM/T
kPtue3GxOgbEPsHffBGssShBTE3yCOX3aAONXJucMrSAPL9iwUfgfGx6ADdkwBsA
OjDlkxvTbP/9trE6/lsSPtGpWRdJNHqXN4Hx7gXJizRwG7Ym+oHvIIh53aIjdFoE
HLQLpxObuQsCgYAuMQ99G83qQpYpc6GwAeYXL4yJyK453kky9z5LMQRt8rKXQhS/
F0FqQYc1vsplW0IZQkQVC5yT0Z4Yz+ICLcM0O9zEVAyA78ZxC42Io9UedSXn9tXK
Awc7IQkHmmxGxm1dZYSEB5X4gFEb+zted3h2ZxMfScohS3zLI70c6a/aYQKBgQCU
phRuxUkrTUpFZ1PCbN0R/ezbpLbaewFTEV7T8b6oxgvxLxI6FdZRcSYO89DNvf2w
GLCVe6VKMWPBTlxPDEostndpjCcTq3vU+nHE+BrBkTvh14BVGzddSFsaYpMvNm8z
ojiJHH2XnCDmefkm6lRacJKL/Tcj4SNmv6YjUEXLDwKBgF8WV9lzez3d/X5dphLy
2S7osRegH99iFanw0v5VK2HqDcYO9A7AD31D9nwX46QVYfgEwa6cHtVCZbpLeJpw
qXnYXe/hUU3yn5ipdNJ0Dm/ZhJPDD8TeqhnRRhxbZmsXs8EzfwB2tcUbASvjb3qA
vAaPlOSU1wXqhAsG9aVs8gtL
-----END PRIVATE KEY-----
""", # 3
"""-----BEGIN CERTIFICATE-----
MIICojCCAYoCAQEwDQYJKoZIhvcNAQELBQAwFzEVMBMGA1UEAwwMbmV3cGJfdGhp
bmd5MB4XDTIwMDEwMjAxNDAzNFoXDTIxMDEwMTAxNDAzNFowFzEVMBMGA1UEAwwM
bmV3cGJfdGhpbmd5MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAzUqQ
M08E7F2ZE99bFHvpsR6LmgIJOOoGMXacTcEUhRF63E6+730FjxER2a30synv9GGS
3G9FstUmfhyimufkbTumri8Novw5CWZQLiE1rmMBI5nPcR2wAzy9z2odR6bfAwms
yyc3IPYg1BEDBPZl0LCQrQRRU/rVOrbCf7IMq+ATazmBg01gXMzq2M953ieorkQX
MsHVR/kyW0Q0yzhYF1OtIqbXxrdiZ+laTLWNqivj/FdegiWPCf8OcqpcpbgEjlDW
gBcC/vre+0E+16nfUV8xHL5jseJMJqfT508OtHxAzp+2D7b54NvYNIvbOAP+F9gj
aXy5mOvjXclK+hNmDwIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQAjZzTFKG7uoXxm
BPHfQvsKHIB/Cx9zMKj6pLwJzCPHQBzKOMoUen09oq+fb77RM7WvdX0pvFgEXaJW
q/ImooRMo+paf8GOZAuPwdafb2/OGdHZGZ2Cbo/ICGo1wGDCdMvbxTxrDNq1Yae+
m+2epN2pXAO1rlc7ktRkojM/qi3zXtbLjTs3IoPDXWhYPHdI1ThkneRmvxpzB1rW
2SBqj2snvyI+/3k3RHmldcdOrTlgWQ9hq05jWR8IVtRUFFVn9A+yQC3gnnLIUhwP
HJWwTIPuYW25TuxFxYZXIbnAiluZL0UIjd3IAwxaafvB6uhI7v0K789DKj2vRUkY
E8ptxZH4
-----END CERTIFICATE-----
-----BEGIN PRIVATE KEY-----
MIIEwAIBADANBgkqhkiG9w0BAQEFAASCBKowggSmAgEAAoIBAQDNSpAzTwTsXZkT
31sUe+mxHouaAgk46gYxdpxNwRSFEXrcTr7vfQWPERHZrfSzKe/0YZLcb0Wy1SZ+
HKKa5+RtO6auLw2i/DkJZlAuITWuYwEjmc9xHbADPL3Pah1Hpt8DCazLJzcg9iDU
EQME9mXQsJCtBFFT+tU6tsJ/sgyr4BNrOYGDTWBczOrYz3neJ6iuRBcywdVH+TJb
RDTLOFgXU60iptfGt2Jn6VpMtY2qK+P8V16CJY8J/w5yqlyluASOUNaAFwL++t77
QT7Xqd9RXzEcvmOx4kwmp9PnTw60fEDOn7YPtvng29g0i9s4A/4X2CNpfLmY6+Nd
yUr6E2YPAgMBAAECggEBAIiL6uQl0AmDrBj6vHMghGzp+0MBza6MgngOA6L4JTTp
ToYQ3pEe4D6rxOq7+QHeiBtNd0ilvn9XpVXGqCVOzrIVNiWvaGubRjjJU9WLA1Ct
y4kpekAr1fIhScMXOsh45ub3XXZ27AVBkM5dTlvTpB8uAd0C/TFVqtR10WLsQ99h
Zm9Jczgs/6InYTssnAaqdeCLAf1LbmO4zwFsJfJOeSGGT6WBwlpHwMAgPhg8OLEu
kVWG7BEJ0hxcODk/es/vce9SN7BSyIzNY+qHcGtsrx/o0eO2Av/Z7ltV4Sz6UN1K
0y0OTiDyT/l62U2OugSN3wQ4xPTwlrWl7ZUHJmvpEaECgYEA+w2JoB2i1OV2JTPl
Y0TKSKcZYdwn7Nwh4fxMAJNJ8UbpPqrZEo37nxqlWNJrY/jKX3wHVk4ESSTaxXgF
UY7yKT0gRuD9+vE0gCbUmJQJTwbceNJUu4XrJ6SBtf72WgmphL+MtyKdwV8XltVl
Yp0hkswGmxl+5+Js6Crh7WznPl8CgYEA0VYtKs2YaSmT1zraY6Fv3AIQZq012vdA
7nVxmQ6jKDdc401OWARmiv0PrZaVNiEJ1YV8KxaPrKTfwhWqxNegmEBgA1FZ66NN
SAm8P9OCbt8alEaVkcATveXTeOCvfpZUO3sqZdDOiYLiLCsokHblkcenK85n0yT6
CzhTbvzDllECgYEAu9mfVy2Vv5OK2b+BLsw0SDSwa2cegL8eo0fzXqLXOzCCKqAQ
GTAgTSbU/idEr+NjGhtmKg/qaQioogVyhVpenLjeQ+rqYDDHxfRIM3rhlD5gDg/j
0wUbtegEHrgOgcSlEW16zzWZsS2EKxq16BoHGx6K+tcS/FOShg5ASzWnuiUCgYEA
sMz+0tLX8aG7CqHbRyBW8FMR9RY/kRMY1Q1+Bw40wMeZfSSSkYYN8T9wWWT/2rqm
qp7V0zJ34BFUJoDUPPH84fok3Uh9EKZYpAoM4z9JP0jREwBWXMYEJnOQWtwxfFGN
DLumgF2Nwtg3G6TL2s+AbtJYH4hxagQl5woIdYmnyzECgYEAsLASpou16A3uXG5J
+5ZgF2appS9Yfrqfh6TKywMsGG/JuiH3djdYhbJFIRGeHIIDb4XEXOHrg/SFflas
If0IjFRh9WCvQxnoRha3/pKRSc3OEka1MR/ZREK/d/LQEPmsRJVzY6ABKqmPAMDD
5CnG6Hz/rP87BiEKd1+3PGp8GCw=
-----END PRIVATE KEY-----
""", # 4
"""-----BEGIN CERTIFICATE-----
MIICojCCAYoCAQEwDQYJKoZIhvcNAQELBQAwFzEVMBMGA1UEAwwMbmV3cGJfdGhp
bmd5MB4XDTIwMDEwMjAxNDAzNFoXDTIxMDEwMTAxNDAzNFowFzEVMBMGA1UEAwwM
bmV3cGJfdGhpbmd5MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0sap
75YbbkEL85LFava3FrO1jpgVteQ4NGxxy1Nu9w2hPfMMeCPWjB8UfAwFk+LVPyvW
LAXd1zWL5rGpQ2ytIVQlTraR5EnALA1sMcQYbFz1ISPTYB031bEN/Ch8JWYwCG5A
X2H4D6BC7NgT6YyWDt8vxQnqAisPHQ/OK4ABD15CwkTyPimek2/ufYN2dapg1xhG
IUD96gqetJv9bu0r869s688kADIComsYG+8KKfFN67S3rSHMIpZPuGTtoHGnVO89
XBm0vNe0UxQkJEGJzZPn0tdec0LTC4GNtTaz5JuCjx/VsJBqrnTnHHjx0wFz8pff
afCimRwA+LCopxPE1QIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQBOkAnpBb3nY+dG
mKCjiLqSsuEPqpNiBYR+ue/8aVDnOKLKqAyQuyRZttQ7bPpKHaw7pwyCZH8iHnt6
pMCLCftNSlV2Fa8msRmuf5AiGjUvR1M8VtHWNYE8pedWrJqUgBhF/405B99yd8CT
kQJXKF18LObj7YKNsWRoMkVgqlQzWDMEqbfmy9MhuLx2EZPsTB1L0BHNGGDVBd9o
cpPLUixcc12u+RPMKq8x3KgwsnUf5vX/pCnoGcCy4JahWdDgcZlf0hUKGT7PUem5
CWW8SMeqSWQX9XpE5Qlm1+W/QXdDXLbbHqDtvBeUy3iFQe3C9RSkp0qdutxkAlFk
f5QHXfJ7
-----END CERTIFICATE-----
-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDSxqnvlhtuQQvz
ksVq9rcWs7WOmBW15Dg0bHHLU273DaE98wx4I9aMHxR8DAWT4tU/K9YsBd3XNYvm
salDbK0hVCVOtpHkScAsDWwxxBhsXPUhI9NgHTfVsQ38KHwlZjAIbkBfYfgPoELs
2BPpjJYO3y/FCeoCKw8dD84rgAEPXkLCRPI+KZ6Tb+59g3Z1qmDXGEYhQP3qCp60
m/1u7Svzr2zrzyQAMgKiaxgb7wop8U3rtLetIcwilk+4ZO2gcadU7z1cGbS817RT
FCQkQYnNk+fS115zQtMLgY21NrPkm4KPH9WwkGqudOccePHTAXPyl99p8KKZHAD4
sKinE8TVAgMBAAECggEALU5EotoqJUXYEtAenUJQ0pFoWjE4oXNf3Wzd/O1/MZ19
ZjqDGKPjbxUTKyLOZB5i5gQ/MhFEwQiifMD9eB+5CyvyJPw7Wc28f/uWoQ/cjBZj
Hm979PHy2X0IW4Y8QTG462b/cUE2t+0j1ZMQnKf6bVHuC7V41mR5CC8oitMl5y5g
34yJmWXlIA0ep/WotLMqvil6DnSM/2V8Ch4SxjnzPpjbe4Kj+woucGNr4UKstZER
8iuHTsR64LjoGktRnnMwZxGZQI7EC428zsliInuWMdXe//w2chLdkirqpSrIQwSZ
3jNWStqBXGYaRg5Z1ilBvHtXxkzDzbAlzRBzqfEwwQKBgQDqYdMRrzHJaXWLdsyU
6jAuNX9tLh7PcicjP93SbPujS6mWcNb+D/au+VhWD+dZQDPRZttXck7wvKY1lw1V
MK0TYI7ydf8h3DFx3Mi6ZD4JVSU1MH233C3fv/FHenDoOvMXXRjUZxaRmuzFJvzt
6QlKIfSvwT+1wrOACNfteXfZUQKBgQDmN3Uuk01qvsETPwtWBp5RNcYhS/zGEQ7o
Q4K+teU453r1v8BGsQrCqulIZ3clMkDru2UroeKn1pzyVAS2AgajgXzfXh3VeZh1
vHTLP91BBYZTTWggalEN4aAkf9bxX/hA+9Bw/dzZcQW2aNV7WrYuCSvp3SDCMina
anQq/PaSRQKBgHjw23HfnegZI89AENaydQQTFNqolrtiYvGcbgC7vakITMzVEwrr
/9VP0pYuBKmYKGTgF0RrNnKgVX+HnxibUmOSSpCv9GNrdJQVYfpT6XL1XYqxp91s
nrs7FuxUMNiUOoWOw1Yuj4W4lH4y3QaCXgnDtbfPFunaOrdRWOIv8HjRAoGAV3NT
mSitbNIfR69YIAqNky3JIJbb42VRc1tJzCYOd+o+pCF96ZyRCNehnDZpZQDM9n8N
9GAfWEBHCCpwS69DVFL422TGEnSJPJglCZwt8OgnWXd7CW05cvt1OMgzHyekhxLg
4Dse7J5pXBxAlAYmVCB5xPGR4xLpISX1EOtcwr0CgYEA5rA2IUfjZYb4mvFHMKyM
xWZuV9mnl3kg0ULttPeOl3ppwjgRbWpyNgOXl8nVMYzxwT/A+xCPA18P0EcgNAWc
frJqQYg3NMf+f0K1wSaswUSLEVrQOj25OZJNpb21JEiNfEd5DinVVj4BtVc6KSpS
kvjbn2WhEUatc3lPL3V0Fkw=
-----END PRIVATE KEY-----
""", # 5
"""-----BEGIN CERTIFICATE-----
MIICojCCAYoCAQEwDQYJKoZIhvcNAQELBQAwFzEVMBMGA1UEAwwMbmV3cGJfdGhp
bmd5MB4XDTIwMDEwMjAxNTExM1oXDTIxMDEwMTAxNTExM1owFzEVMBMGA1UEAwwM
bmV3cGJfdGhpbmd5MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA1c5y
S9IZHF9MIuwdafzhMkgP37I3RVpHEbpnPwnLFqSWelS5m2eDkwWd5SkfGjrmQ5q0
PEpqLlh3zHGw9yQjnHS3CCS1PwQ1kmwvpIK3HM5y8GM7ry1zkam8ZR4iX6Y7VG9g
9mhiVVFoVhe1gHeiC/3Mp6XeNuEiD0buM+8qZx9B21I+iwzy4wva7Gw0fJeq9G1c
lq2rhpD1LlIEodimWOi7lOEkNmUiO1SvpdrGdxUDpTgbdg6r5pCGjOXLd74tAQHP
P/LuqRNJDXtwvHtLIVQnW6wjjy4oiWZ8DXOdc9SkepwQLIF5Wh8O7MzF5hrd6Cvw
SOD3EEsJbyycAob6RwIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQBDNcbKVUyGOAVm
k3iVuzkkkymlTAMm/gsIs6loLJrkSqNg160FdVKJoZFjQtqoqLgLrntdCJ377nZ9
1i+yzbZsA4DA7nxj0IEdnd7rRYgGLspGqWeKSTROATeT4faLTXenecm0v2Rpxqc7
dSyeZJXOd2OoUu+Q64hzXCDXC6LNM+xZufxV9qv+8d+CipV6idSQZaUWSVuqFCwD
PT0R4eWfkMMaM8QqtNot/hVCEaKT+9rG0mbpRe/b/qBy5SR0u+XgGEEIV+33L59T
FXY+DpI1Dpt/bJFoUrfj6XohxdTdqYVCn1F8in98TsRcFHyH1xlkS3Y0RIiznc1C
BwAoGZ4B
-----END CERTIFICATE-----
-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDVznJL0hkcX0wi
7B1p/OEySA/fsjdFWkcRumc/CcsWpJZ6VLmbZ4OTBZ3lKR8aOuZDmrQ8SmouWHfM
cbD3JCOcdLcIJLU/BDWSbC+kgrccznLwYzuvLXORqbxlHiJfpjtUb2D2aGJVUWhW
F7WAd6IL/cynpd424SIPRu4z7ypnH0HbUj6LDPLjC9rsbDR8l6r0bVyWrauGkPUu
UgSh2KZY6LuU4SQ2ZSI7VK+l2sZ3FQOlOBt2DqvmkIaM5ct3vi0BAc8/8u6pE0kN
e3C8e0shVCdbrCOPLiiJZnwNc51z1KR6nBAsgXlaHw7szMXmGt3oK/BI4PcQSwlv
LJwChvpHAgMBAAECggEBAK0KLeUBgIM++Y7WDCRInzYjrn08bpE5tIU7mO4jDfQg
dw1A3wtQZuOpyxW6B0siWlRis/aLv44M2cBkT3ZmEFBDAhOcKfh7fqQn3RNHG847
pDi8B4UKwxskBa7NCcLh9eirUA19hABLJ6dt/t6fdE5CNc2FZ+iAoyE8JfNwYKAd
6Fa3HqUBPNWt8ryj4ftgpMNBdfmLugEM4N20SXJA28hOq2lUcwNKQQ1xQrovl0ig
iMbMWytV4gUPKC9Wra66OYIkk/K8teiUNIYA4JwAUVTs1NEWoyfwUTz1onutCkMl
5vY7JAqRoDWoSUX6FI+IHUdyqPAMdOMhC37gjrxoo2ECgYEA7trDMu6xsOwEckDh
iz148kejMlnTTuCNetOFBw3njFgxISx0PrDLWmJmnHMxPv9AAjXYb2+UCCm3fj6Q
OB8o4ZJm0n504qbFHcb2aI22U5hZ99ERvqx8WBnJ2RarIBmg06y0ktxq8gFR2qxF
0hWAOcDn1DWQ8QI0XBiFFcJTGtcCgYEA5SdlIXRnVZDKi5YufMAORG9i74dXUi0Y
02UoVxJ+q8VFu+TT8wrC5UQehG3gX+79Cz7hthhDqOSCv6zTyE4Evb6vf9OLgnVe
E5iLF033zCxLSS9MgiZ+jTO+wK3RsapXDtGcSEk2P82Pj5seNf4Ei1GNCRlm1DbX
71wlikprHhECgYABqmLcExAIJM0vIsav2uDiB5/atQelMCmsZpcx4mXv85l8GrxA
x6jTW4ZNpvv77Xm7yjZVKJkGqYvPBI6q5YS6dfPjmeAkyHbtazrCpeJUmOZftQSD
qN5BGwTuT5sn4SXe9ABaWdEhGONCPBtMiLvZK0AymaEGHTbSQZWD/lPoBwKBgGhk
qg2zmd/BNoSgxkzOsbE7jTbR0VX+dXDYhKgmJM7b8AjJFkWCgYcwoTZzV+RcW6rj
2q+6HhizAV2QvmpiIIbQd+Mj3EpybYk/1R2ox1qcUy/j/FbOcpihGiVtCjqF/2Mg
2rGTqMMoQl6JrBmsvyU44adjixTiZz0EHZYCkQoBAoGBAMRdmoR4mgIIWFPgSNDM
ISLJxKvSFPYDLyAepLfo38NzKfPB/XuZrcOoMEWRBnLl6dNN0msuzXnPRcn1gc1t
TG7db+hivAyUoRkIW3dB8pRj9dDUqO9OohjKsJxJaQCyH5vPkQFSLbTIgWrHhU+3
oSPiK/YngDV1AOmPDH7i62po
-----END PRIVATE KEY-----
""", #6
"""-----BEGIN CERTIFICATE-----
MIICojCCAYoCAQEwDQYJKoZIhvcNAQELBQAwFzEVMBMGA1UEAwwMbmV3cGJfdGhp
bmd5MB4XDTIwMDEwMjAxNTExMloXDTIxMDEwMTAxNTExMlowFzEVMBMGA1UEAwwM
bmV3cGJfdGhpbmd5MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAojGu
fQaTVT9DJWJ/zogGfrryEJXYVy9c441O5MrLlRx7nCIWIUs2NEhHDJdqJjYOTdmk
K98VhdMpDPZwxjgvvZrh43lStBRIW3zZxv747rSl2VtpSqD/6UNWJe5u4SR7oga4
JfITOKHg/+ASxnOxp/iu6oT6jBL6T7KSPh6Rf2+it2rsjhktRreFDJ2hyroNq1w4
ZVNCcNPgUIyos8u9RQKAWRNchFh0p0FCS9xNrn3e+yHnt+p6mOOF2gMzfXT/M2hq
KQNmc5D3yNoH2smWoz7F3XsRjIB1Ie4VWoRRaGEy7RwcwiDfcaemD0rQug6iqH7N
oomF6f3R4DyvVVLUkQIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQB/8SX6qyKsOyex
v3wubgN3FPyU9PqMfEzrFM6X5sax0VMVbSnekZrrXpdnXYV+3FBu2GLLQc900ojj
vKD+409JIriTcwdFGdLrQPTCRWkEOae8TlXpTxuNqJfCPVNxFN0znoat1bSRsX1U
K0mfEETQ3ARwlTkrF9CM+jkU3k/pnc9MoCLif8P7OAF38AmIbuTUG6Gpzy8RytJn
m5AiA3sds5R0rpGUu8mFeBpT6jIA1QF2g+QNHKOQcfJdCdfqTjKw5y34hjFqbWG9
RxWGeGNZkhC/jADCt+m+R6+hlyboLuIcVp8NJw6CGbr1+k136z/Dj+Fdhm6FzF7B
qULeRQJ+
-----END CERTIFICATE-----
-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQCiMa59BpNVP0Ml
Yn/OiAZ+uvIQldhXL1zjjU7kysuVHHucIhYhSzY0SEcMl2omNg5N2aQr3xWF0ykM
9nDGOC+9muHjeVK0FEhbfNnG/vjutKXZW2lKoP/pQ1Yl7m7hJHuiBrgl8hM4oeD/
4BLGc7Gn+K7qhPqMEvpPspI+HpF/b6K3auyOGS1Gt4UMnaHKug2rXDhlU0Jw0+BQ
jKizy71FAoBZE1yEWHSnQUJL3E2ufd77Iee36nqY44XaAzN9dP8zaGopA2ZzkPfI
2gfayZajPsXdexGMgHUh7hVahFFoYTLtHBzCIN9xp6YPStC6DqKofs2iiYXp/dHg
PK9VUtSRAgMBAAECggEANjn0A3rqUUr4UQxwfIV/3mj0O1VN4kBEhxOcd+PRUsYW
EapXycPSmII9ttj8tU/HUoHcYIqSMI7bn6jZJXxtga/BrALJAsnxMx031k8yvOQK
uvPT7Q6M4NkReVcRHRbMeuxSLuWTRZDhn8qznEPb9rOvD1tsRN6nb3PdbwVbUcZh
2F6JDrTyI/Df6nrYQAWOEe2ay7tzgrNYE4vh+DW7oVmyHRgFYA+DIG5Q+7OVWeW5
bwYYPKlo4/B0L+GfMKfMVZ+5TvFWAK0YD1e/CW1Gv+i/8dWm4O7UNGg5mTnrIcy1
g5wkKbyea02/np2B/XBsSWXDl6rTDHL7ay0rH2hjEQKBgQDMKSm3miQTIcL/F2kG
ieapmRtSc7cedP967IwUfjz4+pxPa4LiU47OCGp1bmUTuJAItyQyu/5O3uLpAriD
PTU+oVlhqt+lI6+SJ4SIYw01/iWI3EF2STwXVnohWG1EgzuFM/EqoB+mrodNONfG
UmP58vI9Is8fdugXgpTz4Yq9pQKBgQDLYJoyMVrYTvUn5oWft8ptsWZn6JZXt5Bd
aXh+YhNmtCrSORL3XjcH4yjlcn7X8Op33WQTbPo7QAJ1CumJzAI88BZ/8za638xb
nLueviZApCt0bNMEEdxDffxHFc5TyHE+obMKFfApbCnD0ggO6lrZ8jK9prArLOCp
mRU9SSRffQKBgAjoBszeqZI4F9SfBdLmMyzU5A89wxBOFFMdfKLsOua1sBn627PZ
51Hvpg1HaptoosfujWK1NsvkB0wY9UmsYuU/jrGnDaibnO4oUSzN/WaMlsCYszZg
zYFLIXrQ67tgajlOYcf1Qkw4MujYgPlC4N+njI/EM/rwagGUjcDx5uaNAoGASyqz
EuYG63eTSGH89SEaohw0+yaNmnHv23aF4EAjZ4wjX3tUtTSPJk0g6ly84Nbb8d1T
hZJ7kbaAsf2Mfy91jEw4JKYhjkP05c8x0OP6g12p6efmvdRUEmXX/fXjQjgNEtb0
sz+UedrOPN+9trWLSo4njsyyw+JcTpKTtQj5dokCgYEAg9Y3msg+GvR5t/rPVlKd
keZkrAp0xBJZgqG7CTPXWS1FjwbAPo7x4ZOwtsgjCuE52lar4j+r2Il+CDYeLfxN
h/Jfn6S9ThUh+B1PMvKMMnJUahg8cVL8uQuBcbAy8HPRK78WO2BTnje44wFAJwTc
0liuYqVxZIRlFLRl8nGqog8=
-----END PRIVATE KEY-----
""", #7
"""-----BEGIN CERTIFICATE-----
MIICojCCAYoCAQEwDQYJKoZIhvcNAQELBQAwFzEVMBMGA1UEAwwMbmV3cGJfdGhp
bmd5MB4XDTIwMDEwMjAxNTExMloXDTIxMDEwMTAxNTExMlowFzEVMBMGA1UEAwwM
bmV3cGJfdGhpbmd5MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAu9oO
cFlNukUcLfFrfkEaUiilcHLmn5OokQbj95CGd2ehQCCVwrkunYLBisthRaancFFb
/yM998B0IUsKTsoLi5DAN3/SkSm6GiQIGO05E4eBPljwJ61QQMxh8+1TwQ9HTun1
ZE1lhVN1aRmI9VsbyTQLjXh9OFNLSJEKb29mXsgzYwYwNOvo+idzXpy4bMyNoGxY
Y+s2FIKehNHHCv4ravDn8rf6DtDOvyN4d0/QyNws9FpAZMXmLwtBJ9exOqKFW43w
97NxgdNiTFyttrTKTi0b+9v3GVdcEZw5b2RMIKi6ZzPof6/0OlThK6C3xzFK3Bp4
PMjTfXw5yyRGVBnZZwIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQA4Ms6LqzMu757z
bxISiErRls6fcnq0fpSmiPNHNKM7YwG9KHYwPT6A0UMt30zDwNOXCQBI19caGeeO
MLPWa7Gcqm2XZB2jQwvLRPeFSy9fm6RzJFeyhrh/uFEwUetwYmi/cqeIFDRDBQKn
bOaXkBk0AaSmI5nRYfuqpMMjaKOFIFcoADw4l9wWhv6DmnrqANzIdsvoSXi5m8RL
FcZQDZyHFlHh3P3tLkmQ7ErM2/JDwWWPEEJMlDm/q47FTOQSXZksTI3WRqbbKVv3
iQlJjpgi9yAuxZwoM3M4975iWH4LCZVMCSqmKCBt1h9wv4LxqX/3kfZhRdy1gG+j
41NOSwJ/
-----END CERTIFICATE-----
-----BEGIN PRIVATE KEY-----
MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQC72g5wWU26RRwt
8Wt+QRpSKKVwcuafk6iRBuP3kIZ3Z6FAIJXCuS6dgsGKy2FFpqdwUVv/Iz33wHQh
SwpOyguLkMA3f9KRKboaJAgY7TkTh4E+WPAnrVBAzGHz7VPBD0dO6fVkTWWFU3Vp
GYj1WxvJNAuNeH04U0tIkQpvb2ZeyDNjBjA06+j6J3NenLhszI2gbFhj6zYUgp6E
0ccK/itq8Ofyt/oO0M6/I3h3T9DI3Cz0WkBkxeYvC0En17E6ooVbjfD3s3GB02JM
XK22tMpOLRv72/cZV1wRnDlvZEwgqLpnM+h/r/Q6VOEroLfHMUrcGng8yNN9fDnL
JEZUGdlnAgMBAAECggEALlZdlW0R9U6y4spYf65Dddy84n4VUWu0+wE+HoUyBiYz
6oOfLYdMbmIgp8H/XpT7XINVNBxXXtPEUaoXAtRoAKdWItqO8Gvgki4tKSjrGVwl
j2GU69SepT1FNExoiojgSCEB/RnyXu71WVWJKSyuL/V8nAsKqGgze9T7Q/2wvNQt
SQqLxZlrWF0P8WqaAiSrHV4GnDrdeF+k1KBo2+pSaDNv6cNwOyVG8EII9tqhF8kj
6nD6846ish6OqmlSisaSGopJZL1DCQzszFMxKd2+iBDY7Kn6hVIhRaNnaZUFhpKM
dNh6hBqOycMepAp0sz5pdo+fxpifkoR/cPWgyC3QkQKBgQDixe9VsiZ7u2joxF/9
JcAExKhqE28OUmIwt6/j+uzYShxN6Oo9FUo3ICtAPCCFsjhvb3Qum7FspmxrqtNy
fzclibZJPO8ey2PzqaiOfiVfgJmNSvoCOdgM4OqFLtRO6eSTzhJeI4VPrPcq/5la
0FuOi1WZs/Au9llqLqGSDH3UAwKBgQDUD/bSJbOk5SvNjFtFm0ClVJr66mJ5e4uN
4VGv8KGFAJ+ExIxujAukfKdwLjS1wEy2RePcshfT8Y9FVh/Q1KzzrQi3Gwmfq1G6
Dpu2HlJpaZl+9T81x2KS8GP3QNczWMe2nh7Lj+6st+b4F+6FYbVTFnHaae27sXrD
XPX15+uxzQKBgGy+pBWBF4kwBo/QU4NuTdU7hNNRPGkuwl1ASH1Xv6m8aDRII8Nk
6TDkITltW98g5oUxehI7oOpMKCO9SCZYsNY0YpBeQwCOYgDfc6/Y+A0C+x9RO/BD
UsJiPLPfD/pDmNPz9sTj3bKma+RXq29sCOujD0pkiiHLCnerotkJWnGHAoGAAkCJ
JoIv/jhQ1sX+0iZr8VWMr819bjzZppAWBgBQNtFi4E4WD7Z9CSopvQ9AkA2SwvzL
BrT9e8q88sePXvBjRdM4nHk1CPUQ0SEGllCMH4J3ltmT6kZLzbOv3BhcMLdop4/W
U+MbbcomMcxPRCtdeZxraR5m3+9qlliOZCYqYqECgYA5eLdxgyHxCS33QGFHRvXI
TLAHIrr7wK1xwgkmZlLzYSQ8Oqh1UEbgoMt4ulRczP2g7TCfvANw2Sw0H2Q5a6Fj
cnwVcXJ38DLg0GCPMwzE8dK7d8tKtV6kGiKy+KFvoKChPjE6uxhKKmCJaSwtQEPS
vsjX3iiIgUQPsSz8RrNFfQ==
-----END PRIVATE KEY-----
""", #8
"""-----BEGIN CERTIFICATE-----
MIICojCCAYoCAQEwDQYJKoZIhvcNAQELBQAwFzEVMBMGA1UEAwwMbmV3cGJfdGhp
bmd5MB4XDTIwMDEwMjAxNTExMloXDTIxMDEwMTAxNTExMlowFzEVMBMGA1UEAwwM
bmV3cGJfdGhpbmd5MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA5DNu
CKhhl6wCbgoCkFemwJh3ATbAjhInHpvQWIFDfSK1USElCKxqosIxiBQCx3Zs2d/U
GeIA7QAM2atNdXaateacEaKMmGE9LEtO0Dg5lmT43WzmGkG9NmCwK3JjAekc5S9d
HKNtEQo7o8RKfj81zlDSq2kzliy98cimk24VBBGkS2Cn7Vy/mxMCqWjQazTXbpoS
lXw6LiY5wFXQmXOB5GTSHvqyCtBQbOSSbJB77z/fm7bufTDObufTbJIq53WPt00Y
f+JNnzkX1X0MaBCUztoZwoMaExWucMe/7xsQ46hDn6KB4b0lZk+gsK45QHxvPE1R
72+ZkkIrGS/ljIKahQIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQDib1653CneSmy2
gYzGeMlrI05Jqo3JuHNMQHzAjIrb4ee57VA4PTQa1ygGol/hVv6eTvZr3p2ospDS
5Kfwj1HLO4jSMX1Bnm1FG0naQogz2CD3xfYjbYOVRhAxpld1MNyRveIOhDRARY7N
XNAaNPZ1ALrwbENSYArr18xDzgGWe/dgyRCEpCFIsztiA+7jGvrmAZgceIE8K3h3
fkvNmXBH58ZHAGTiyRriBZqS+DXrBrQOztXSJwFnOZnRt6/efeBupt8j5hxVpBLW
vtjpBc23uUcbbHOY2AW2Bf+vIr4/LmJ/MheKV+maa2990vmC93tvWlFfc74mgUkW
HJfXDmR6
-----END CERTIFICATE-----
-----BEGIN PRIVATE KEY-----
MIIEvwIBADANBgkqhkiG9w0BAQEFAASCBKkwggSlAgEAAoIBAQDkM24IqGGXrAJu
CgKQV6bAmHcBNsCOEicem9BYgUN9IrVRISUIrGqiwjGIFALHdmzZ39QZ4gDtAAzZ
q011dpq15pwRooyYYT0sS07QODmWZPjdbOYaQb02YLArcmMB6RzlL10co20RCjuj
xEp+PzXOUNKraTOWLL3xyKaTbhUEEaRLYKftXL+bEwKpaNBrNNdumhKVfDouJjnA
VdCZc4HkZNIe+rIK0FBs5JJskHvvP9+btu59MM5u59NskirndY+3TRh/4k2fORfV
fQxoEJTO2hnCgxoTFa5wx7/vGxDjqEOfooHhvSVmT6CwrjlAfG88TVHvb5mSQisZ
L+WMgpqFAgMBAAECggEABTdPuo7uvCLIY2+DI319aEWT4sk3mYe8sSxqlLtPqZqT
fmk9iXc3cMTzkOK0NY71af19waGy17f6kzchLCAr5SCCTLzkbc87MLn/8S530oI4
VgdZMxxxkL6hCD0zGiYT7QEqJa9unMcZGeMwuLYFKtQaHKTo8vPO26n0dMY9YLxj
cNUxsKLcKk8dbfKKt4B4fZgB7nU0BG9YbKYZ3iZ7/3mG+6jA6u+VYc/WHYQjTmpL
oLFN7NOe3R7jIx/kJ1OqNWqsFoLpyiiWd1Mr0l3EdD1kCudptMgD8hd++nx2Yk2w
K4+CpOVIN/eCxDDaAOJgYjCtOayVwUkDAxRRt9VnAQKBgQD5s1j6RJtBNTlChVxS
W3WpcG4q8933AiUY/Chx0YTyopOiTi7AGUaA8AOLFBcO2npa+vzC+lvuOyrgOtVW
sD10H2v5jNKlbeBp+Q9rux2LAyp4TvzdXWKhVyZrdtITF0hn6vEYNp7MtyWRFb1O
3Ie5HQBPHtzllFOMynacjOdjpQKBgQDp9TrbfOmwGWmwPKmaFKuy8BKxjJM+ct0X
4Xs1uSy9Z9Y8QlDNbNaooI8DA1NY0jDVHwemiGC4bYsBNKNRcbI0s2nr0hQMft42
P/NpugHv0YXiVz+5bfim4woTiHHbfREqchlIGo3ryClAiDU9fYZwTOtb9jPIhX3G
9v+OsoMlYQKBgQDJUQW90S5zJlwh+69xXvfAQjswOimNCpeqSzK4gTn0/YqV4v7i
Nf6X2eqhaPMmMJNRYuYCtSMFMYLiAc0a9UC2rNa6/gSfB7VU+06phtTMzSKimNxa
BP6OIduB7Ox2I+Fmlw8GfJMPbeHF1YcpW7e5UV58a9+g4TNzYZC7qwarWQKBgQCA
FFaCbmHonCD18F/REFvm+/Lf7Ft3pp5PQouXH6bUkhIArzVZIKpramqgdaOdToSZ
SAGCM8rvbFja8hwurBWpMEdeaIW9SX8RJ/Vz/fateYDYJnemZgPoKQcNJnded5t8
Jzab+J2VZODgiTDMVvnQZOu8To6OyjXPRM0nK6cMQQKBgQDyX44PHRRhEXDgJFLU
qp2ODL54Qadc/thp2m+JmAvqmCCLwuYlGpRKVkLLuZW9W6RlVqarOC3VD3wX5PRZ
IsyCGLi+Jbrv9JIrYUXE80xNeQVNhrrf02OW0KHbqGxRaNOmp1THPw98VUGR2J/q
YAp6XUXU7LEBUrowye+Ty2o7Lg==
-----END PRIVATE KEY-----
""", #9
"""-----BEGIN CERTIFICATE-----
MIICojCCAYoCAQEwDQYJKoZIhvcNAQELBQAwFzEVMBMGA1UEAwwMbmV3cGJfdGhp
bmd5MB4XDTIwMDEwMjAxNTExMVoXDTIxMDEwMTAxNTExMVowFzEVMBMGA1UEAwwM
bmV3cGJfdGhpbmd5MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA1k2R
PWYihftppo3CoxeseFwgg7guxZVkP7aAur5uBzSeAB7sBG1G2bRrwMX71S4xPwot
zYiEoxUrTStUqEKjL2aozfHsXnHZ7kwwUgZFDZUg+ve2tZDA3HCUr4tLYKlyFqpx
2nCouc45MjQ4wAxRl4rQxIUG2uSTzvP+xXtjoJYMIEEyCpcsRXfqfVkEUe9nrPsF
0Ibzk7Cyt75HDI4uEzBuHux0DYuGy6R02jz/vf/dIZ4WepjSY06xpblTHZgieDRX
fU2+YOcvb0eDHyA8Q5p8ropK71MNIP5+kffFd90SVr4EkCA8S+cd6FdKQasRr+jF
9MUhMS4ObvlrYTG+hwIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQCy62MZ3+59/VpX
c9Hsmb4/BMWt0irLJit4w4SkuYKGFLCMKZI4LN4pEkXaiE1eqF2DNS1qOvl5luty
Zz4oggrqilwuFeH98o9Zeg9SYnouuypORVP/3DPbJF/jiQg5J8kJb1sy+BjRiT8I
5X6/cCBYT+MljFz5tpqWOtWTgA30e1BV8JFj8F4dgUcWsAVT/I4l9zgMLUnhcO6E
wLtEE0I6aT1RHJB28ndwJzj4La98Oirw7LAEAWbExWYB90ypLaGY+JVJe3f5fijC
fJpQ2mbs4syXDmb5bU2C2pGPTKZPcyx15iQrq1uHInD0facOw+pmllAFxuG96lA1
+o2VzKwP
-----END CERTIFICATE-----
-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDWTZE9ZiKF+2mm
jcKjF6x4XCCDuC7FlWQ/toC6vm4HNJ4AHuwEbUbZtGvAxfvVLjE/Ci3NiISjFStN
K1SoQqMvZqjN8execdnuTDBSBkUNlSD697a1kMDccJSvi0tgqXIWqnHacKi5zjky
NDjADFGXitDEhQba5JPO8/7Fe2OglgwgQTIKlyxFd+p9WQRR72es+wXQhvOTsLK3
vkcMji4TMG4e7HQNi4bLpHTaPP+9/90hnhZ6mNJjTrGluVMdmCJ4NFd9Tb5g5y9v
R4MfIDxDmnyuikrvUw0g/n6R98V33RJWvgSQIDxL5x3oV0pBqxGv6MX0xSExLg5u
+WthMb6HAgMBAAECggEAeCyRSNwQeg/NZD/UqP6qkegft52+ZMBssinWsGH/c3z3
KVwtwCHDfGvnjPe5TAeWSCKeIsbukkFZwfGNjLmppvgrqymCAkhYDICfDDBF4uMA
1pu40sJ01Gkxh+tV/sOmnb1BEVzh0Sgq/NM6C8ActR18CugKOw+5L3G2KeoSqUbT
2hcPUsnik10KwqW737GQW4LtEQEr/iRmQkxI3+HBzvPWjFZzjOcpUph+FW5TXtaU
T26mt1j+FjbdvvhCuRMY/VZBJ5h1RKU95r57F1AjW/C0RRJ8FxR1CeSy4IlmQBrh
6wAa3Tdm0k/n4ZspC9bF5eVTJEtb0AohiYZrIa8MuQKBgQD8yjCLYa41H304odCx
NwPRJcmlIk5YGxPrhHAT9GEgU6n/no7YMVx1L7fNLcMjAyx54jauEU7J19Aki7eV
SIdU9TwqmkOAFfM6TOEJZiOi66gABOxeK2yDyfmR6Apaw3caku4O058t4KVwHSCB
DanYCMzxCBqS9jUTTyAh0fMg6wKBgQDZBkIukg3FKPor5LzkUXIKnNHYPfHbERHw
piWS6GZwqhuWNlOCWxiBR4rEUU/RbFQZw/FCi5OuAk2lBC0LBmC0/Sz4/+xDdCbv
uNhMOTRcy9nFVpmpIWCx4N/KmXHEuFxli/JNXux7iki74AVC9VPrAt/kCvwf06Df
oDb8ljdR1QKBgQChVOD6c5Lc8IXYeN1Z3IShHH6+11AsxstFyjZFZff+y6Z5L1Z2
/7nESHoDhqs9Uy81cnv3R7CC/Ssnx8uYiLtmK0UE44Mk4d1jXeFZQEiKF+AWcw3v
Y8NTsLmItxC0sH75BMDN0Z2LiA3Nqaku8+trpuI1Cjj7hgqFkkAtlXKXlQKBgBMb
c/Q5s7CqHOyEZQUNDqdUiz0opwSMijHPzvsSLwK4V1lwSwXtE0k+jT8fkZF0oirq
j3E2bLgjR8bBiV2xIA6PQ8hgb+K4dT0h3xlG6A9Le07egwTbBXJjxBBIVjXlrWzb
V2fsdZGi6ShxXsU4aD0GscOYG/6JWV6W8oBmkVRJAoGAepIZ+OYmFjb7uxdh4EtP
hluEtx5bLOLuo6c0S149omUXUhbsuyzTZS6Ip9ySDMnK3954c4Q4WJ4yQKixQNVq
78aDfy4hP/8TE/Q9CRddUof2P33PJMhVNqzTRYMpqV+zxifvtw3hoDTLKHTQxCR2
M1+O4VvokU5pBqUpGXiMDfs=
-----END PRIVATE KEY-----
""", #10
"""-----BEGIN CERTIFICATE-----
MIICojCCAYoCAQEwDQYJKoZIhvcNAQELBQAwFzEVMBMGA1UEAwwMbmV3cGJfdGhp
bmd5MB4XDTIwMDEwMjAxNTExMVoXDTIxMDEwMTAxNTExMVowFzEVMBMGA1UEAwwM
bmV3cGJfdGhpbmd5MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAnbCU
M37hG7zrCyyJEI6pZmOomnI+CozbP5KAhWSV5y7R5H6lcAEG2UDV+lCUxHT2ufOa
i1H16bXyBt7VoMTHIH50S58NUCUEXcuRWVR16tr8CzcTHQAkfIrmhY2XffPilX7h
aw35UkoVmXcqSDNNJD6jmvWexvmbhzVWW8Vt5Pivet2/leVuqPXB54/alSbkC74m
x6X5XKQc6eyPsb1xvNBuiSpFzdqbEn7lUwj6jFTkh9tlixgmgx+J0XoQXbawyrAg
rcIQcse/Ww+KBA1KSccFze+XBTbIull4boYhbJqkb6DW5bY7/me2nNxE9DRGwq+S
kBsKq3YKeCf8LEhfqQIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQAD+tWGFhINYsWT
ibKWlCGgBc5uB7611cLCevx1yAL6SaOECVCQXzaaXIaETSbyY03UO2yBy3Pl10FV
GYXLrAWTFZsNVJm55XIibTNw1UBPNwdIoCSzAYuOgMF0GHhTTQU0hNYWstOnnE2T
6lSAZQZFkaW4ZKs6sUp42Em9Bu99PehyIgnw14qb9NPg5qKdi2GAvkImZCrGpMdK
OF31U7Ob0XQ0lxykcNgG4LlUACd+QxLfNpmLBZUGfikexYa1VqBFm3oAvTt8ybNQ
qr7AKXDFnW75aCBaMpQWzrstA7yYZ3D9XCd5ZNf6d08lGM/oerDAIGnZOZPJgs5U
FaWPHdS9
-----END CERTIFICATE-----
-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQCdsJQzfuEbvOsL
LIkQjqlmY6iacj4KjNs/koCFZJXnLtHkfqVwAQbZQNX6UJTEdPa585qLUfXptfIG
3tWgxMcgfnRLnw1QJQRdy5FZVHXq2vwLNxMdACR8iuaFjZd98+KVfuFrDflSShWZ
dypIM00kPqOa9Z7G+ZuHNVZbxW3k+K963b+V5W6o9cHnj9qVJuQLvibHpflcpBzp
7I+xvXG80G6JKkXN2psSfuVTCPqMVOSH22WLGCaDH4nRehBdtrDKsCCtwhByx79b
D4oEDUpJxwXN75cFNsi6WXhuhiFsmqRvoNbltjv+Z7ac3ET0NEbCr5KQGwqrdgp4
J/wsSF+pAgMBAAECggEAPSu1ofBTRN5ZU4FYPlsJLdX1Hsy4coFHv/aF8rkdSYwp
EflrFfLgBEEZgLvnqfoxh9sPFYKa4amaFL42ouIS2PEVDgzKLk/dzMDeRof0IkIG
yhb4TCS1ArcjS6WsociNGi8ZJN1L3Xctv9WxSkbUYv4Fm2Qyzr8fbSjssjb5NXwD
K11fsj6Pfy/mQrI0TSTlzWC7ARIlCMTWQ8G8zEU6bMFIG6DMjt2J4VgYVXUKetZA
VPuS+pwkH2obQe6FLRuiNxH4GitVAASYPea6foER4AggBMRp8q8F6+WssjoyEORb
0sJxmnxhznoTRMCuTsUj6XMgmOTOnA3lQXsIB0DYcQKBgQDO6mMRVVFWzgkE9Q5/
36n06KvGYF9TCRDL9vRC8kCqcGd1Hy6jRj0D8049KUHaN74pfWg6gsQjPkKzwKnC
vxNl72tVvLqm7Fo531BGfKK/46ZvxeWMMraNW4+9LhwMPu2LN5OEdwwCgyaURpxh
ktCp+RrGjz08Kn82X1jJPdwxDQKBgQDDGMvZ7ZUDGq5+RJkmHJ58lQtiaMZclmYV
R9YwOxJV6ino3EYrGOtUkqiemgAACdMWE/JMJlB1/JINawJwUsZ2XDp/9jNLPgLc
gphCmagaO34U/YMaJbJIK2gkCX7p8EcD+x45qWa0bEMPW38QfN/qQdUPjNmpuIiI
Zleyl1TqDQKBgQCvIoat0ighsAzETGN0aqzhJdrW8xVcJA06hpFi5MdFPBTldno0
KqxUXqj3badWe94SIhqJg8teBUHSAZ3uv2o82nRgQnk99km8OD8rGi1q+9YRP1C2
5OnNJhW4y4FkABNxxZ2v/k+FBNsvn8CXefvyEm3OaMks1s+MBxIQa7KnNQKBgFwX
HUo+GiN/+bPCf6P8yFa4J8qI+HEF0SPkZ9cWWx5QzP2M1FZNie++1nce7DcYbBo0
yh9lyn8W/H328AzDFckS2c5DEY1HtSQPRP3S+AWB5Y7U54h1GMV2L88q6ExWzb60
T10aeE9b9v+NydmniC5UatTPQIMbht8Tp/u18TAVAoGBAJphAfiqWWV2M5aBCSXq
WxLZ71AJ0PZBmRa/9iwtccwXQpMcW6wHK3YSQxci+sB97TElRa3/onlVSpohrUtg
VCvCwfSHX1LmrfWNSkoJZwCQt+YYuMqW86K0tzLzI1EMjIH9LgQvB6RR26PZQs+E
jr1ZvRc+wPTq6sxCF1h9ZAfN
-----END PRIVATE KEY-----
""", #11
]

# To disable the pre-computed tub certs, uncomment this line.
# SYSTEM_TEST_CERTS = []


def flush_but_dont_ignore(res):
    d = flushEventualQueue()
    def _done(ignored):
        return res
    d.addCallback(_done)
    return d


def _render_config(config):
    """
    Convert a ``dict`` of ``dict`` of ``unicode`` to an ini-format string.
    """
    return u"\n\n".join(list(
        _render_config_section(k, v)
        for (k, v)
        in config.items()
    ))

def _render_config_section(heading, values):
    """
    Convert a ``unicode`` heading and a ``dict`` of ``unicode`` to an ini-format
    section as ``unicode``.
    """
    return u"[{}]\n{}\n".format(
        heading, _render_section_values(values)
    )

def _render_section_values(values):
    """
    Convert a ``dict`` of ``unicode`` to the body of an ini-format section as
    ``unicode``.
    """
    return u"\n".join(list(
        u"{} = {}".format(k, v)
        for (k, v)
        in sorted(values.items())
    ))


@async_to_deferred
async def spin_until_cleanup_done(value=None, timeout=10):
    """
    At the end of the test, spin until the reactor has no more DelayedCalls
    and file descriptors (or equivalents) registered. This prevents dirty
    reactor errors, while also not hard-coding a fixed amount of time, so it
    can finish faster on faster computers.

    There is also a timeout: if it takes more than 10 seconds (by default) for
    the remaining reactor state to clean itself up, the presumption is that it
    will never get cleaned up and the spinning stops.

    Make sure to run as last thing in tearDown.
    """
    def num_fds():
        if hasattr(reactor, "handles"):
            # IOCP!
            return len(reactor.handles)
        else:
            # Normal reactor; having internal readers still registered is fine,
            # that's not our code.
            return len(
                set(reactor.getReaders()) - set(reactor._internalReaders)
            ) + len(reactor.getWriters())

    for i in range(timeout * 1000):
        # There's a single DelayedCall for AsynchronousDeferredRunTest's
        # timeout...
        if (len(reactor.getDelayedCalls()) < 2 and num_fds() == 0):
            break
        await deferLater(reactor, 0.001)
    return value


class SystemTestMixin(pollmixin.PollMixin, testutil.StallMixin):

    # If set to True, use Foolscap for storage protocol. If set to False, HTTP
    # will be used when possible. If set to None, this suggests a bug in the
    # test code.
    FORCE_FOOLSCAP_FOR_STORAGE : Optional[bool] = None

    # If True, reduce the timeout on connections:
    REDUCE_HTTP_CLIENT_TIMEOUT : bool = True

    def setUp(self):
        if os.getenv("TAHOE_DEBUG_BLOCKING") == "1":
            from .blocking import catch_blocking_in_event_loop
            catch_blocking_in_event_loop(self)

        self._http_client_pools = []
        http_client.StorageClientFactory.start_test_mode(self._got_new_http_connection_pool)
        self.addCleanup(http_client.StorageClientFactory.stop_test_mode)
        self.port_assigner = SameProcessStreamEndpointAssigner()
        self.port_assigner.setUp()
        self.addCleanup(self.port_assigner.tearDown)

        self.sparent = service.MultiService()
        self.sparent.startService()

    def _got_new_http_connection_pool(self, pool):
        # Register the pool for shutdown later:
        self._http_client_pools.append(pool)
        # Disable retries:
        pool.retryAutomatically = False
        # Make a much more aggressive timeout for connections, we're connecting
        # locally after all... and also make sure it's lower than the delay we
        # add in tearDown, to prevent dirty reactor issues.
        getConnection = pool.getConnection

        def getConnectionWithTimeout(*args, **kwargs):
            d = getConnection(*args, **kwargs)
            d.addTimeout(1, reactor)
            return d

        if self.REDUCE_HTTP_CLIENT_TIMEOUT:
            pool.getConnection = getConnectionWithTimeout

    def close_idle_http_connections(self):
        """Close all HTTP client connections that are just hanging around."""
        return defer.gatherResults(
            [pool.closeCachedConnections() for pool in self._http_client_pools]
        )

    def tearDown(self):
        log.msg("shutting down SystemTest services")
        d = self.sparent.stopService()
        d.addBoth(flush_but_dont_ignore)
        d.addBoth(lambda x: self.close_idle_http_connections().addCallback(lambda _: x))
        d.addBoth(spin_until_cleanup_done)
        return d

    def getdir(self, subdir):
        return os.path.join(self.basedir, subdir)

    def add_service(self, s):
        s.setServiceParent(self.sparent)
        return s

    def _create_introducer(self):
        """
        :returns: (via Deferred) an Introducer instance
        """
        iv_dir = self.getdir("introducer")
        if not os.path.isdir(iv_dir):
            _, web_port_endpoint = self.port_assigner.assign(reactor)
            main_location_hint, main_port_endpoint = self.port_assigner.assign(reactor)
            introducer_config = (
                u"[node]\n"
                u"nickname = introducer \N{BLACK SMILING FACE}\n" +
                u"web.port = {}\n".format(web_port_endpoint) +
                u"tub.port = {}\n".format(main_port_endpoint) +
                u"tub.location = {}\n".format(main_location_hint)
            ).encode("utf-8")

            fileutil.make_dirs(iv_dir)
            fileutil.write(
                os.path.join(iv_dir, 'tahoe.cfg'),
                introducer_config,
            )
            if SYSTEM_TEST_CERTS:
                os.mkdir(os.path.join(iv_dir, "private"))
                f = open(os.path.join(iv_dir, "private", "node.pem"), "w")
                f.write(SYSTEM_TEST_CERTS[0])
                f.close()
        return create_introducer(basedir=iv_dir)

    def _get_introducer_web(self):
        with open(os.path.join(self.getdir("introducer"), "node.url"), "r") as f:
            return f.read().strip()

    @inlineCallbacks
    def set_up_nodes(self, NUMCLIENTS=5):
        """
        Create an introducer and ``NUMCLIENTS`` client nodes pointed at it.  All
        of the nodes are running in this process.

        As a side-effect, set:

        * ``numclients`` to ``NUMCLIENTS``
        * ``introducer`` to the ``_IntroducerNode`` instance
        * ``introweb_url`` to the introducer's HTTP API endpoint.

        :param int NUMCLIENTS: The number of client nodes to create.

        :return: A ``Deferred`` that fires when the nodes have connected to
            each other.
        """
        self.assertIn(
            self.FORCE_FOOLSCAP_FOR_STORAGE, (True, False),
            "You forgot to set FORCE_FOOLSCAP_FOR_STORAGE on {}".format(self.__class__)
        )
        self.numclients = NUMCLIENTS

        self.introducer = yield self._create_introducer()
        self.add_service(self.introducer)
        self.introweb_url = self._get_introducer_web()
        yield self._set_up_client_nodes(self.FORCE_FOOLSCAP_FOR_STORAGE)
        native_server = next(iter(self.clients[0].storage_broker.get_known_servers()))
        if self.FORCE_FOOLSCAP_FOR_STORAGE:
            expected_storage_server_class = NativeStorageServer
        else:
            expected_storage_server_class = HTTPNativeStorageServer
        self.assertIsInstance(native_server, expected_storage_server_class)

    @inlineCallbacks
    def _set_up_client_nodes(self, force_foolscap):
        q = self.introducer
        self.introducer_furl = q.introducer_url
        self.clients = []
        basedirs = []
        for i in range(self.numclients):
            basedirs.append((yield self._set_up_client_node(i, force_foolscap)))

        # start clients[0], wait for it's tub to be ready (at which point it
        # will have registered the helper furl).
        c = yield client.create_client(basedirs[0])
        c.setServiceParent(self.sparent)
        self.clients.append(c)

        with open(os.path.join(basedirs[0],"private","helper.furl"), "r") as f:
            helper_furl = f.read()

        self.helper_furl = helper_furl
        if self.numclients >= 2:
            with open(os.path.join(basedirs[1], 'tahoe.cfg'), 'a+') as f:
                f.write(
                    "[client]\n"
                    "helper.furl = {}\n".format(helper_furl)
                )

        # this starts the rest of the clients
        for i in range(1, self.numclients):
            c = yield client.create_client(basedirs[i])
            c.setServiceParent(self.sparent)
            self.clients.append(c)
        log.msg("STARTING")
        yield self.wait_for_connections()
        log.msg("CONNECTED")
        # now find out where the web port was
        self.webish_url = self.clients[0].getServiceNamed("webish").getURL()
        if self.numclients >=2:
            # and the helper-using webport
            self.helper_webish_url = self.clients[1].getServiceNamed("webish").getURL()

    def _generate_config(self, which, basedir, force_foolscap=False):
        config = {}

        allclients = set(range(self.numclients))
        except1 = allclients - {1}
        feature_matrix = {
            ("client", "nickname"): except1,

            # Auto-assigning addresses is extremely failure prone and not
            # amenable to automated testing in _this_ manner.
            ("node", "tub.port"): allclients,
            ("node", "tub.location"): allclients,

            # client 0 runs a webserver and a helper
            # client 1 runs a webserver but no helper
            ("node", "web.port"): {0, 1},
            ("node", "timeout.keepalive"): {0},
            ("node", "timeout.disconnect"): {1},

            ("helper", "enabled"): {0},
        }

        def setconf(config, which, section, feature, value):
            if which in feature_matrix.get((section, feature), {which}):
                config.setdefault(section, {})[feature] = value

        setnode = partial(setconf, config, which, "node")
        sethelper = partial(setconf, config, which, "helper")

        setnode("nickname", u"client %d \N{BLACK SMILING FACE}" % (which,))
        setconf(config, which, "storage", "force_foolscap", str(force_foolscap))
        setconf(config, which, "client", "force_foolscap", str(force_foolscap))

        tub_location_hint, tub_port_endpoint = self.port_assigner.assign(reactor)
        setnode("tub.port", tub_port_endpoint)
        setnode("tub.location", tub_location_hint)

        _, web_port_endpoint = self.port_assigner.assign(reactor)
        setnode("web.port", web_port_endpoint)
        setnode("timeout.keepalive", "600")
        setnode("timeout.disconnect", "1800")

        sethelper("enabled", "True")

        iyaml = ("introducers:\n"
                 " petname2:\n"
                 "  furl: %s\n") % self.introducer_furl
        iyaml_fn = os.path.join(basedir, "private", "introducers.yaml")
        fileutil.write(iyaml_fn, iyaml)
        return _render_config(config)

    def _set_up_client_node(self, which, force_foolscap):
        basedir = self.getdir("client%d" % (which,))
        fileutil.make_dirs(os.path.join(basedir, "private"))
        if len(SYSTEM_TEST_CERTS) > (which + 1):
            f = open(os.path.join(basedir, "private", "node.pem"), "w")
            f.write(SYSTEM_TEST_CERTS[which + 1])
            f.close()
        config = self._generate_config(which, basedir, force_foolscap)
        fileutil.write(os.path.join(basedir, 'tahoe.cfg'), config)
        return basedir

    def bounce_client(self, num):
        c = self.clients[num]
        d = c.disownServiceParent()
        # I think windows requires a moment to let the connection really stop
        # and the port number made available for re-use. TODO: examine the
        # behavior, see if this is really the problem, see if we can do
        # better than blindly waiting for a second.
        d.addCallback(self.stall, 1.0)

        @defer.inlineCallbacks
        def _stopped(res):
            new_c = yield client.create_client(self.getdir("client%d" % num))
            self.clients[num] = new_c
            new_c.setServiceParent(self.sparent)
        d.addCallback(_stopped)
        d.addCallback(lambda res: self.wait_for_connections())
        def _maybe_get_webport(res):
            if num == 0:
                # now find out where the web port was
                self.webish_url = self.clients[0].getServiceNamed("webish").getURL()
        d.addCallback(_maybe_get_webport)
        return d

    @defer.inlineCallbacks
    def add_extra_node(self, client_num, helper_furl=None,
                       add_to_sparent=False):
        # usually this node is *not* parented to our self.sparent, so we can
        # shut it down separately from the rest, to exercise the
        # connection-lost code
        basedir = FilePath(self.getdir("client%d" % client_num))
        basedir.makedirs()
        config = (
            "[node]\n"
            "tub.location = {}\n"
            "tub.port = {}\n"
            "[client]\n"
        ).format(*self.port_assigner.assign(reactor))

        if helper_furl:
            config += "helper.furl = %s\n" % helper_furl
        basedir.child("tahoe.cfg").setContent(config.encode("utf-8"))
        private = basedir.child("private")
        private.makedirs()
        write_introducer(
            basedir,
            "default",
            self.introducer_furl,
        )

        c = yield client.create_client(basedir.path)
        self.clients.append(c)
        self.numclients += 1
        if add_to_sparent:
            c.setServiceParent(self.sparent)
        else:
            c.startService()
        yield self.wait_for_connections()
        defer.returnValue(c)

    def _check_connections(self):
        for i, c in enumerate(self.clients):
            if not c.connected_to_introducer():
                log.msg("%s not connected to introducer yet" % (i,))
                return False
            sb = c.get_storage_broker()
            connected_servers = sb.get_connected_servers()
            connected_names = sorted(list(
                connected.get_nickname()
                for connected
                in sb.get_known_servers()
                if connected.is_connected()
            ))
            if len(connected_servers) != self.numclients:
                wanted = sorted(list(
                    client.nickname
                    for client
                    in self.clients
                ))
                log.msg(
                    "client %s storage broker connected to %s, missing %s" % (
                        i,
                        connected_names,
                        set(wanted) - set(connected_names),
                    )
                )
                return False
            log.msg("client %s storage broker connected to %s, happy" % (
                i, connected_names,
            ))
            up = c.getServiceNamed("uploader")
            if up._helper_furl and not up._helper:
                log.msg("Helper fURL but no helper")
                return False
        return True

    def wait_for_connections(self, ignored=None):
        return self.poll(self._check_connections, timeout=200)
