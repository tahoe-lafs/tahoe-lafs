**********************
Magic Wormhole Invites
**********************

Magic Wormhole
==============

`magic wormhole`_ is a server and a client which together use Password
Authenticated Key Exchange (PAKE) to use a short code to establish a
secure channel between two computers. These codes are one-time use and
an attacker gets at most one "guess", thus allowing low-entropy codes
to be used.

.. _magic wormhole: https://github.com/warner/magic-wormhole#design


Invites and Joins
=================

Inside Tahoe-LAFS we are using a channel created using `magic
wormhole`_ to exchange configuration and the secret fURL of the
Introducer with new clients.

This is a two-part process. Alice runs a grid and wishes to have her
friend Bob use it as a client. She runs ``tahoe invite bob`` which
will print out a short "wormhole code" like ``2-unicorn-quiver``. You
may also include some options for total, happy and needed shares if
you like.

Alice then transmits this one-time secret code to Bob. Alice must keep
her command running until Bob has done his step as it is waiting until
a secure channel is established before sending the data.

Bob then runs ``tahoe create-client --join <secret code>`` with any
other options he likes. This will "use up" the code establishing a
secure session with Alice's computer. If an attacker tries to guess
the code, they get only once chance to do so (and then Bob's side will
fail). Once Bob's computer has connected to Alice's computer, the two
computers performs the protocol described below, resulting in some
JSON with the Introducer fURL, nickname and any other options being
sent to Bob's computer. The ``tahoe create-client`` command then uses
these options to set up Bob's client.



Tahoe-LAFS Secret Exchange
==========================

The protocol that the Alice (the one doing the invite) and Bob (the
one being invited) sides perform once a magic wormhole secure channel
has been established goes as follows:

Alice and Bob both immediately send an "abilities" message as
JSON. For Alice this is ``{"abilities": {"server-v1": {}}}``. For Bob,
this is ``{"abilities": {"client-v1": {}}}``.

After receiving the message from the other side and confirming the
expected protocol, Alice transmits the configuration JSON::

    {
        "needed": 3,
        "total": 10,
        "happy": 7,
        "nickname": "bob",
        "introducer": "pb://xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx@example.com:41505/yyyyyyyyyyyyyyyyyyyyyyy"
    }

Both sides then disconnect.

As you can see, there is room for future revisions of the protocol but
as of yet none have been sketched out.
