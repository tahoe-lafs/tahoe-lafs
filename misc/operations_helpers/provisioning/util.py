# -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-
# -*- indent-tabs-mode: nil -*-

import os.path

def sibling(filename):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
