Preparing to Authenticate Release (Setting up GPG)
--------------------------------------------------

In other to keep releases authentic it's required that releases are signed before being
published. This ensure's that users of Tahoe are able to verify that the version of Tahoe
they are using is coming from a trusted or at the very least known source.

The authentication is done using the ``GPG`` implementation of ``OpenGPG`` to be able to complete 
the release steps you would have to download the ``GPG`` software and setup a key(identity).

- `Download <https://www.gnupg.org/download/>`__ and install GPG for your operating system.
- Generate a key pair using ``gpg --gen-key``. *Some questions would be asked to personalize your key configuration.*

You might take additional steps including:

- Setting up a revocation certificate (Incase you lose your secret key)
- Backing up your key pair
- Upload your fingerprint to a keyserver such as `openpgp.org <https://keys.openpgp.org/>`__
