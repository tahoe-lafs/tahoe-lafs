The implementation of SDMF and MDMF (mutables) now requires RSA keys to be exactly 2048 bits, aligning them with the specification.

Some code existed to allow tests to shorten this and it's
conceptually possible a modified client produced mutables
with different key-sizes. However, the spec says that they
must be 2048 bits. If you happen to have a capability with
a key-size different from 2048 you may use 1.17.1 or earlier
to read the content.
