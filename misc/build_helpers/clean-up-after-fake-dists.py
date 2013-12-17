# -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-
import glob, os, shutil

if os.path.exists('support'):
    shutil.rmtree('support')

[shutil.rmtree(p) for p in glob.glob('pycryptopp*.egg')]
