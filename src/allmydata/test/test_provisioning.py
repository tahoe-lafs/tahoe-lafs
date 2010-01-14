
from twisted.trial import unittest
from allmydata import provisioning
ReliabilityModel = None
try:
    from allmydata.reliability import ReliabilityModel
except ImportError:
    pass # might not be importable, since it needs NumPy

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
        self.failUnlessEqual(type(lots_of_stan), list)

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
        self.failUnlessEqual(type(more_stan), list)

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
            raise unittest.SkipTest("reliability model requires NumPy")

        # test that numpy math works the way I think it does
        import numpy
        decay = numpy.matrix([[1,0,0],
                             [.1,.9,0],
                             [.01,.09,.9],
                             ])
        start = numpy.array([0,0,1])
        g2 = (start * decay).A[0]
        self.failUnlessEqual(repr(g2), repr(numpy.array([.01,.09,.9])))
        g3 = (g2 * decay).A[0]
        self.failUnlessEqual(repr(g3), repr(numpy.array([.028,.162,.81])))

        # and the dot product
        recoverable = numpy.array([0,1,1])
        P_recoverable_g2 = numpy.dot(g2, recoverable)
        self.failUnlessAlmostEqual(P_recoverable_g2, .9 + .09)
        P_recoverable_g3 = numpy.dot(g3, recoverable)
        self.failUnlessAlmostEqual(P_recoverable_g3, .81 + .162)

        r = ReliabilityModel.run(delta=100000,
                                 report_period=3*MONTH,
                                 report_span=5*YEAR)
        self.failUnlessEqual(len(r.samples), 20)

        last_row = r.samples[-1]
        #print last_row
        (when, unmaintained_shareprobs, maintained_shareprobs,
         P_repaired_last_check_period,
         cumulative_number_of_repairs,
         cumulative_number_of_new_shares,
         P_dead_unmaintained, P_dead_maintained) = last_row
        self.failUnless(isinstance(P_repaired_last_check_period, float))
        self.failUnless(isinstance(P_dead_unmaintained, float))
        self.failUnless(isinstance(P_dead_maintained, float))
        self.failUnlessAlmostEqual(P_dead_unmaintained, 0.033591004555395272)
        self.failUnlessAlmostEqual(P_dead_maintained, 3.2983995819177542e-08)

