
from twisted.trial import unittest
from allmydata import provisioning
ReliabilityModel = None
try:
    from allmydata.reliability import ReliabilityModel
except ImportError:
    pass # might not be importable, since it needs Numeric

from nevow import inevow
from zope.interface import implements

class MyRequest:
    implements(inevow.IRequest)
    pass

class Provisioning(unittest.TestCase):
    def getarg(self, name, astype=int):
        if name in self.fields:
            return astype(self.fields[name])
        return None

    def test_load(self):
        pt = provisioning.ProvisioningTool()
        self.fields = {}
        #r = MyRequest()
        #r.fields = self.fields
        #ctx = RequestContext()
        #unfilled = pt.renderSynchronously(ctx)
        lots_of_stan = pt.do_forms(self.getarg)

        self.fields = {'filled': True,
                       "num_users": 50e3,
                       "files_per_user": 1000,
                       "space_per_user": 1e9,
                       "sharing_ratio": 1.0,
                       "encoding_parameters": "3-of-10-5",
                       "num_servers": 30,
                       "ownership_mode": "A",
                       "download_rate": 100,
                       "upload_rate": 10,
                       "delete_rate": 10,
                       "lease_timer": 7,
                       }
        #filled = pt.renderSynchronously(ctx)
        more_stan = pt.do_forms(self.getarg)

        # trigger the wraparound configuration
        self.fields["num_servers"] = 5
        #filled = pt.renderSynchronously(ctx)
        more_stan = pt.do_forms(self.getarg)

        # and other ownership modes
        self.fields["ownership_mode"] = "B"
        more_stan = pt.do_forms(self.getarg)
        self.fields["ownership_mode"] = "E"
        more_stan = pt.do_forms(self.getarg)

    def test_provisioning_math(self):
        self.failUnlessEqual(provisioning.binomial(10, 0), 1)
        self.failUnlessEqual(provisioning.binomial(10, 1), 10)
        self.failUnlessEqual(provisioning.binomial(10, 2), 45)
        self.failUnlessEqual(provisioning.binomial(10, 9), 10)
        self.failUnlessEqual(provisioning.binomial(10, 10), 1)

DAY=24*60*60
MONTH=31*DAY
YEAR=365*DAY

class Reliability(unittest.TestCase):
    def test_basic(self):
        if ReliabilityModel is None:
            raise unittest.SkipTest("reliability model requires Numeric")
        r = ReliabilityModel.run(delta=100000,
                                 report_period=3*MONTH,
                                 report_span=5*YEAR)
        self.failUnlessEqual(len(r.samples), 20)
