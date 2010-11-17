import glob, os, shutil

if os.path.exists('support'):
    shutil.rmtree('support')

[shutil.rmtree(p) for p in glob.glob('pycryptopp*.egg')]
