Magic Folder user interface design
==================================

Scope
-----

In this Objective we will design a user interface to allow users to conveniently
and securely indicate which folders on some devices should be "magically" linked
to which folders on other devices.

This is a critical usability and security issue for which there is no known perfect
solution, but which we believe is amenable to a "good enough" trade-off solution.
This document explains the design and justifies its trade-offs in terms of security,
usability, and time-to-market.

Tickets on the Tahoe-LAFS trac with the `otf-magic-folder-objective6`_
keyword are within the scope of the user interface design.

.. _otf-magic-folder-objective6: https://tahoe-lafs.org/trac/tahoe-lafs/query?status=!closed&keywords=~otf-magic-folder-objective6

Glossary
''''''''

Object: a file or directory

DMD: distributed mutable directory

Folder: an abstract directory that is synchronized between clients.
(A folder is not the same as the directory corresponding to it on
any particular client, nor is it the same as a DMD.)

Collective: the set of clients subscribed to a given Magic Folder.

Diminishing: the process of deriving, from an existing capability,
another capability that gives less authority (for example, deriving a
read cap from a read/write cap).


Design Constraints
------------------

The design of the Tahoe-side representation of a Magic Folder, and the polling
mechanism that the Magic Folder clients will use to detect remote changes was
discussed in `<remote-to-local-sync.rst>`_, and we will not revisit that here.
The assumption made by that design was that each client would be configured with
the following information:

* a write cap to its own *client DMD*.
* a read cap to a *collective directory*.

The collective directory contains links to each client DMD named by the
corresponding client's nickname.

This design was chosen to allow straightforward addition of clients without
requiring each existing client to change its configuration.

Note that each client in a Magic Folder collective has the authority to add,
modify or delete any object within the Magic Folder. It is also able to control
to some extent whether its writes will be treated by another client as overwrites
or as conflicts. However, there is still a reliability benefit to preventing a
client from accidentally modifying another client's DMD, or from accidentally
modifying the collective directory in a way that would lose data. This motivates
ensuring that each client only has access to the caps above, rather than, say,
every client having a write cap to the collective directory.

Another important design constraint is that we cannot violate the
`write coordination directive`_; that is, we cannot write to the same mutable
directory from multiple clients, even during the setup phase when adding a
client.

.. _`write coordination directive`: ../../write_coordination.rst

Within these constraints, for usability we want to minimize the number of steps
required to configure a Magic Folder collective.


Proposed Design
---------------

Three ``tahoe`` subcommands are added::

  tahoe magic-folder create MAGIC: [MY_NICKNAME LOCAL_DIR]

    Create an empty Magic Folder. The MAGIC: local alias is set
    to a write cap which can be used to refer to this Magic Folder
    in future ``tahoe magic-folder invite`` commands.

    If MY_NICKNAME and LOCAL_DIR are given, the current client
    immediately joins the newly created Magic Folder with that
    nickname and local directory.


  tahoe magic-folder invite MAGIC: THEIR_NICKNAME

    Print an "invitation" that can be used to invite another
    client to join a Magic Folder, with the given nickname.

    The invitation must be sent to the user of the other client
    over a secure channel (e.g. PGP email, OTR, or ssh).

    This command will normally be run by the same client that
    created the Magic Folder. However, it may be run by a
    different client if the ``MAGIC:`` alias is copied to
    the ``private/aliases`` file of that other client, or if
    ``MAGIC:`` is replaced by the write cap to which it points.


  tahoe magic-folder join INVITATION LOCAL_DIR

    Accept an invitation created by ``tahoe magic-folder invite``.
    The current client joins the specified Magic Folder, which will
    appear in the local filesystem at the given directory.


There are no commands to remove a client or to revoke an
invitation, although those are possible features that could
be added in future. (When removing a client, it is necessary
to copy each file it added to some other client's DMD, if it
is the most recent version of that file.)


Implementation
''''''''''''''

For "``tahoe magic-folder create MAGIC: [MY_NICKNAME LOCAL_DIR]``" :

1. Run "``tahoe create-alias MAGIC:``".
2. If ``MY_NICKNAME`` and ``LOCAL_DIR`` are given, do the equivalent of::

     INVITATION=`tahoe invite-magic-folder MAGIC: MY_NICKNAME`
     tahoe join-magic-folder INVITATION LOCAL_DIR


For "``tahoe magic-folder invite COLLECTIVE_WRITECAP NICKNAME``" :

(``COLLECTIVE_WRITECAP`` can, as a special case, be an alias such as ``MAGIC:``.)

1. Create an empty client DMD. Let its write URI be ``CLIENT_WRITECAP``.
2. Diminish ``CLIENT_WRITECAP`` to ``CLIENT_READCAP``, and
   diminish ``COLLECTIVE_WRITECAP`` to ``COLLECTIVE_READCAP``.
3. Run "``tahoe ln CLIENT_READCAP COLLECTIVE_WRITECAP/NICKNAME``".
4. Print "``COLLECTIVE_READCAP+CLIENT_WRITECAP``" as the invitation,
   accompanied by instructions on how to accept the invitation and
   the need to send it over a secure channel.


For "``tahoe magic-folder join INVITATION LOCAL_DIR``" :

1. Parse ``INVITATION`` as ``COLLECTIVE_READCAP+CLIENT_WRITECAP``.
2. Write ``CLIENT_WRITECAP`` to the file ``magic_folder_dircap``
   under the client's ``private`` directory.
3. Write ``COLLECTIVE_READCAP`` to the file ``collective_dircap``
   under the client's ``private`` directory.
4. Edit the client's ``tahoe.cfg`` to set
   ``[magic_folder] enabled = True`` and
   ``[magic_folder] local.directory = LOCAL_DIR``.


Discussion
----------

The proposed design has a minor violation of the
`Principle of Least Authority`_ in order to reduce the number
of steps needed. The invoker of "``tahoe magic-folder invite``"
creates the client DMD on behalf of the invited client, and
could retain its write cap (which is part of the invitation).

.. _`Principle of Least Authority`: http://www.eros-os.org/papers/secnotsep.pdf

A possible alternative design would be for the invited client
to create its own client DMD, and send it back to the inviter
to be linked into the collective directory. However this would
require another secure communication and another command
invocation per client. Given that, as mentioned earlier, each
client in a Magic Folder collective already has the authority
to add, modify or delete any object within the Magic Folder,
we considered the potential security/reliability improvement
here not to be worth the loss of usability.

We also considered a design where each client had write access
to the collective directory. This would arguably be a more
serious violation of the Principle of Least Authority than the
one above (because all clients would have excess authority rather
than just the inviter). In any case, it was not clear how to make
such a design satisfy the `write coordination directive`_,
because the collective directory would have needed to be written
to by multiple clients.

The reliance on a secure channel to send the invitation to its
intended recipient is not ideal, since it may involve additional
software such as clients for PGP, OTR, ssh etc. However, we believe
that this complexity is necessary rather than incidental, because
there must be some way to distinguish the intended recipient from
potential attackers who would try to become members of the Magic
Folder collective without authorization. By making use of existing
channels that have likely already been set up by security-conscious
users, we avoid reinventing the wheel or imposing substantial extra
implementation costs.

The length of an invitation will be approximately the combined
length of a Tahoe-LAFS read cap and write cap. This is several
lines long, but still short enough to be cut-and-pasted successfully
if care is taken. Errors in copying the invitation can be detected
since Tahoe-LAFS cap URIs are self-authenticating.

The implementation of the ``tahoe`` subcommands is straightforward
and raises no further difficult design issues.
