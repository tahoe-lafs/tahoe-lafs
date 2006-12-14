#! /usr/bin/python

from twisted.trial import unittest
from twisted.internet import defer
from allmydata import encode_new
from cStringIO import StringIO

class MyEncoder(encode_new.Encoder):
    def send(self, share_num, methname, *args, **kwargs):
        if False and share_num < 10:
            print "send[%d].%s()" % (share_num, methname)
            if methname == "put_share_hashes":
                print " ", [i for i,h in args[0]]
        return defer.succeed(None)

class Encode(unittest.TestCase):
    def test_1(self):
        e = MyEncoder()
        data = StringIO("some data to encode\n")
        e.setup(data)
        d = e.start()
        return d

