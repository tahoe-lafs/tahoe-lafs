from base64 import b32encode, b32decode

def b2a(i):
    return b32encode(i).lower()

def a2b(i):
    return b32decode(i.upper())
