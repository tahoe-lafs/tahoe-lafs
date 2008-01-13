#! /usr/bin/env python
'''
Unit tests for tahoe-fuse.

Note: The API design of the python-fuse library makes unit testing much
of tahoe-fuse.py tricky business.
'''

import unittest

import tahoe_fuse


class TestUtilFunctions (unittest.TestCase):
    '''Tests small stand-alone functions.'''
    def test_canonicalize_cap(self):
        iopairs = [('http://127.0.0.1:8123/uri/URI:DIR2:yar9nnzsho6czczieeesc65sry:upp1pmypwxits3w9izkszgo1zbdnsyk3nm6h7e19s7os7s6yhh9y',
                    'URI:DIR2:yar9nnzsho6czczieeesc65sry:upp1pmypwxits3w9izkszgo1zbdnsyk3nm6h7e19s7os7s6yhh9y'),
                   ('http://127.0.0.1:8123/uri/URI%3ACHK%3Ak7ktp1qr7szmt98s1y3ha61d9w%3A8tiy8drttp65u79pjn7hs31po83e514zifdejidyeo1ee8nsqfyy%3A3%3A12%3A242?filename=welcome.html',
                    'URI:CHK:k7ktp1qr7szmt98s1y3ha61d9w:8tiy8drttp65u79pjn7hs31po83e514zifdejidyeo1ee8nsqfyy:3:12:242?filename=welcome.html')]

        for input, output in iopairs:
            result = tahoe_fuse.canonicalize_cap(input)
            self.failUnlessEqual(output, result, 'input == %r' % (input,))
                    




if __name__ == '__main__':
    unittest.main()
