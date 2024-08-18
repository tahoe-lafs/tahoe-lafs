.. -*- coding: utf-8-with-signature -*-

==================================
User-Visible Changes in Tahoe-LAFS
==================================

.. towncrier start line
Release 1.190 (2024-01-04)
''''''''''''''''''''''''''

Features
--------

- Tahoe-LAFS now includes a new "Grid Manager" specification and implementation adding more options to control which storage servers a client will use for uploads. (`#2916 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2916>`_)
- Added support for Python 3.12, and work with Eliot 1.15 (`#3072 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3072>`_)
- `tahoe run ...` will now exit when its stdin is closed.

  This facilitates subprocess management, specifically cleanup.
  When a parent process is running tahoe and exits without time to do "proper" cleanup at least the stdin descriptor will be closed.
  Subsequently "tahoe run" notices this and exits. (`#3921 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3921>`_)
- Mutable objects can now be created with a pre-determined "signature key" using the ``tahoe put`` CLI or the HTTP API.  This enables deterministic creation of mutable capabilities.  This feature must be used with care to preserve the normal security and reliability properties. (`#3962 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3962>`_)
- Added support for Python 3.11. (`#3982 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3982>`_)
- tahoe run now accepts --allow-stdin-close to mean "keep running if stdin closes" (`#4036 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4036>`_)
- The storage server and client now support a new, HTTPS-based protocol. (`#4041 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4041>`_)
- Started work on a new end-to-end benchmarking framework. (`#4060 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4060>`_)
- Some operations now run in threads, improving the responsiveness of Tahoe nodes. (`#4068 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4068>`_)
- Logs are now written in a thread, which should make the application more responsive under load. (`#4804 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4804>`_)


Bug Fixes
---------

- Provide better feedback from plugin configuration errors

  Local errors now print a useful message and exit.
  Announcements that only contain invalid / unusable plugins now show a message in the Welcome page. (`#3899 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3899>`_)
- Work with (and require) newer versions of pycddl. (`#3938 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3938>`_)
- Uploading immutables will now better use available bandwidth, which should allow for faster uploads in many cases. (`#3939 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3939>`_)
- Downloads of large immutables should now finish much faster. (`#3946 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3946>`_)
- Fix incompatibility with transitive dependency charset_normalizer >= 3  when using PyInstaller. (`#3966 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3966>`_)
- A bug where Introducer nodes configured to listen on Tor or I2P would not actually do so has been fixed. (`#3999 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3999>`_)
- The (still off-by-default) HTTP storage client will now use Tor when Tor-based client-side anonymity was requested.
  Previously it would use normal TCP connections and not be anonymous. (`#4029 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4029>`_)
- Provide our own copy of attrs' "provides()" validator

  This validator is deprecated and slated for removal; that project's suggestion is to copy the code to our project. (`#4056 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4056>`_)
- Fix a race condition with SegmentFetcher (`#4078 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4078>`_)


Dependency/Installation Changes
-------------------------------

- tenacity is no longer a dependency. (`#3989 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3989>`_)


Documentation Changes
---------------------

- Several minor errors in the Great Black Swamp proposed specification document have been fixed. (`#3922 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3922>`_)
- Document the ``force_foolscap`` configuration options for ``[storage]`` and ``[client]``. (`#4039 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4039>`_)


Removed Features
----------------

- Python 3.7 is no longer supported, and Debian 10 and Ubuntu 18.04 are no longer tested. (`#3964 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3964>`_)


Other Changes
-------------

- The integration test suite now includes a set of capability test vectors (``integration/vectors/test_vectors.yaml``) which can be used to verify compatibility between Tahoe-LAFS and other implementations. (`#3961 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3961>`_)


Misc/Other
----------

- `#3508 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3508>`_, `#3622 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3622>`_, `#3783 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3783>`_, `#3870 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3870>`_, `#3874 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3874>`_, `#3880 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3880>`_, `#3904 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3904>`_, `#3910 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3910>`_, `#3914 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3914>`_, `#3917 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3917>`_, `#3927 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3927>`_, `#3928 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3928>`_, `#3935 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3935>`_, `#3936 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3936>`_, `#3937 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3937>`_, `#3940 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3940>`_, `#3942 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3942>`_, `#3944 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3944>`_, `#3947 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3947>`_, `#3950 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3950>`_, `#3952 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3952>`_, `#3953 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3953>`_, `#3954 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3954>`_, `#3956 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3956>`_, `#3958 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3958>`_, `#3959 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3959>`_, `#3960 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3960>`_, `#3965 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3965>`_, `#3967 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3967>`_, `#3968 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3968>`_, `#3969 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3969>`_, `#3970 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3970>`_, `#3971 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3971>`_, `#3974 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3974>`_, `#3975 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3975>`_, `#3976 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3976>`_, `#3978 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3978>`_, `#3987 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3987>`_, `#3988 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3988>`_, `#3991 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3991>`_, `#3993 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3993>`_, `#3994 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3994>`_, `#3996 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3996>`_, `#3998 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3998>`_, `#4000 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4000>`_, `#4001 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4001>`_, `#4002 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4002>`_, `#4003 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4003>`_, `#4004 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4004>`_, `#4005 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4005>`_, `#4006 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4006>`_, `#4009 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4009>`_, `#4010 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4010>`_, `#4012 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4012>`_, `#4014 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4014>`_, `#4015 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4015>`_, `#4016 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4016>`_, `#4018 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4018>`_, `#4019 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4019>`_, `#4020 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4020>`_, `#4022 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4022>`_, `#4023 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4023>`_, `#4024 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4024>`_, `#4026 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4026>`_, `#4027 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4027>`_, `#4028 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4028>`_, `#4035 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4035>`_, `#4038 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4038>`_, `#4040 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4040>`_, `#4042 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4042>`_, `#4044 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4044>`_, `#4046 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4046>`_, `#4047 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4047>`_, `#4049 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4049>`_, `#4050 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4050>`_, `#4051 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4051>`_, `#4052 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4052>`_, `#4055 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4055>`_, `#4059 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4059>`_, `#4061 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4061>`_, `#4062 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4062>`_, `#4063 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4063>`_, `#4065 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4065>`_, `#4066 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4066>`_, `#4070 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4070>`_, `#4074 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4074>`_, `#4075 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4075>`_


Release 1.18.0 (2022-10-02)
'''''''''''''''''''''''''''

Backwards Incompatible Changes
------------------------------

- Python 3.6 is no longer supported, as it has reached end-of-life and is no longer receiving security updates. (`#3865 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3865>`_)
- Python 3.7 or later is now required; Python 2 is no longer supported. (`#3873 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3873>`_)
- Share corruption reports stored on disk are now always encoded in UTF-8. (`#3879 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3879>`_)
- Record both the PID and the process creation-time:

  a new kind of pidfile in `running.process` records both
  the PID and the creation-time of the process. This facilitates
  automatic discovery of a "stale" pidfile that points to a
  currently-running process. If the recorded creation-time matches
  the creation-time of the running process, then it is a still-running
  `tahoe run` process. Otherwise, the file is stale.

  The `twistd.pid` file is no longer present. (`#3926 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3926>`_)


Features
--------

- The implementation of SDMF and MDMF (mutables) now requires RSA keys to be exactly 2048 bits, aligning them with the specification.

  Some code existed to allow tests to shorten this and it's
  conceptually possible a modified client produced mutables
  with different key-sizes. However, the spec says that they
  must be 2048 bits. If you happen to have a capability with
  a key-size different from 2048 you may use 1.17.1 or earlier
  to read the content. (`#3828 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3828>`_)
- "make" based release automation (`#3846 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3846>`_)


Misc/Other
----------

- `#3327 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3327>`_, `#3526 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3526>`_, `#3697 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3697>`_, `#3709 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3709>`_, `#3786 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3786>`_, `#3788 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3788>`_, `#3802 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3802>`_, `#3816 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3816>`_, `#3855 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3855>`_, `#3858 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3858>`_, `#3859 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3859>`_, `#3860 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3860>`_, `#3867 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3867>`_, `#3868 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3868>`_, `#3871 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3871>`_, `#3872 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3872>`_, `#3875 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3875>`_, `#3876 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3876>`_, `#3877 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3877>`_, `#3881 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3881>`_, `#3882 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3882>`_, `#3883 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3883>`_, `#3889 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3889>`_, `#3890 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3890>`_, `#3891 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3891>`_, `#3893 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3893>`_, `#3895 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3895>`_, `#3896 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3896>`_, `#3898 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3898>`_, `#3900 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3900>`_, `#3909 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3909>`_, `#3913 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3913>`_, `#3915 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3915>`_, `#3916 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3916>`_


Release 1.17.1 (2022-01-07)
'''''''''''''''''''''''''''

Bug Fixes
---------

- Fixed regression on Python 3 causing the JSON version of the Welcome page to sometimes produce a 500 error (`#3852 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3852>`_)
- Fixed regression on Python 3 where JSON HTTP POSTs failed to be processed. (`#3854 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3854>`_)


Misc/Other
----------

- `#3848 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3848>`_, `#3849 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3849>`_, `#3850 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3850>`_, `#3856 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3856>`_


Release 1.17.0 (2021-12-06)
'''''''''''''''''''''''''''

Security-related Changes
------------------------

- The introducer server no longer writes the sensitive introducer fURL value to its log at startup time.  Instead it writes the well-known path of the file from which this value can be read. (`#3819 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3819>`_)
- The storage protocol operation ``add_lease`` now safely rejects an attempt to add a 4,294,967,296th lease to an immutable share.
  Previously this failed with an error after recording the new lease in the share file, resulting in the share file losing track of a one previous lease. (`#3821 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3821>`_)
- The storage protocol operation ``readv`` now safely rejects attempts to read negative lengths.
  Previously these read requests were satisfied with the complete contents of the share file (including trailing metadata) starting from the specified offset. (`#3822 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3822>`_)
- The storage server implementation now respects the ``reserved_space`` configuration value when writing lease information and recording corruption advisories.
  Previously, new leases could be created and written to disk even when the storage server had less remaining space than the configured reserve space value.
  Now this operation will fail with an exception and the lease will not be created.
  Similarly, if there is no space available, corruption advisories will be logged but not written to disk. (`#3823 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3823>`_)
- The storage server implementation no longer records corruption advisories about storage indexes for which it holds no shares. (`#3824 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3824>`_)
- The lease-checker now uses JSON instead of pickle to serialize its state.

  tahoe will now refuse to run until you either delete all pickle files or
  migrate them using the new command::

      tahoe admin migrate-crawler

  This will migrate all crawler-related pickle files. (`#3825 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3825>`_)
- The SFTP server no longer accepts password-based credentials for authentication.
  Public/private key-based credentials are now the only supported authentication type.
  This removes plaintext password storage from the SFTP credentials file.
  It also removes a possible timing side-channel vulnerability which might have allowed attackers to discover an account's plaintext password. (`#3827 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3827>`_)
- The storage server now keeps hashes of lease renew and cancel secrets for immutable share files instead of keeping the original secrets. (`#3839 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3839>`_)
- The storage server now keeps hashes of lease renew and cancel secrets for mutable share files instead of keeping the original secrets. (`#3841 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3841>`_)


Features
--------

- Tahoe-LAFS releases now have just a .tar.gz source release and a (universal) wheel (`#3735 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3735>`_)
- tahoe-lafs now provides its statistics also in OpenMetrics format (for Prometheus et. al.) at `/statistics?t=openmetrics`. (`#3786 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3786>`_)
- If uploading an immutable hasn't had a write for 30 minutes, the storage server will abort the upload. (`#3807 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3807>`_)


Bug Fixes
---------

- When uploading an immutable, overlapping writes that include conflicting data are rejected. In practice, this likely didn't happen in real-world usage. (`#3801 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3801>`_)


Dependency/Installation Changes
-------------------------------

- Tahoe-LAFS now supports running on NixOS 21.05 with Python 3. (`#3808 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3808>`_)


Documentation Changes
---------------------

- The news file for future releases will include a section for changes with a security impact. (`#3815 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3815>`_)


Removed Features
----------------

- The little-used "control port" has been removed from all node types. (`#3814 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3814>`_)


Other Changes
-------------

- Tahoe-LAFS no longer runs its Tor integration test suite on Python 2 due to the increased complexity of obtaining compatible versions of necessary dependencies. (`#3837 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3837>`_)


Misc/Other
----------

- `#3525 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3525>`_, `#3527 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3527>`_, `#3754 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3754>`_, `#3758 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3758>`_, `#3784 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3784>`_, `#3792 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3792>`_, `#3793 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3793>`_, `#3795 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3795>`_, `#3797 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3797>`_, `#3798 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3798>`_, `#3799 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3799>`_, `#3800 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3800>`_, `#3805 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3805>`_, `#3806 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3806>`_, `#3810 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3810>`_, `#3812 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3812>`_, `#3820 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3820>`_, `#3829 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3829>`_, `#3830 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3830>`_, `#3831 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3831>`_, `#3832 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3832>`_, `#3833 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3833>`_, `#3834 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3834>`_, `#3835 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3835>`_, `#3836 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3836>`_, `#3838 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3838>`_, `#3842 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3842>`_, `#3843 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3843>`_, `#3847 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3847>`_


Release 1.16.0 (2021-09-17)
'''''''''''''''''''''''''''

Backwards Incompatible Changes
------------------------------

- The Tahoe command line now always uses UTF-8 to decode its arguments, regardless of locale. (`#3588 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3588>`_)
- tahoe backup's --exclude-from has been renamed to --exclude-from-utf-8, and correspondingly requires the file to be UTF-8 encoded. (`#3716 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3716>`_)


Features
--------

- Added 'typechecks' environment for tox running mypy and performing static typechecks. (`#3399 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3399>`_)
- The NixOS-packaged Tahoe-LAFS now knows its own version. (`#3629 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3629>`_)


Bug Fixes
---------

- Fix regression that broke flogtool results on Python 2. (`#3509 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3509>`_)
- Fix a logging regression on Python 2 involving unicode strings. (`#3510 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3510>`_)
- Certain implementation-internal weakref KeyErrors are now handled and should no longer cause user-initiated operations to fail. (`#3539 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3539>`_)
- SFTP public key auth likely works more consistently, and SFTP in general was previously broken. (`#3584 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3584>`_)
- Fixed issue where redirecting old-style URIs (/uri/?uri=...) didn't work. (`#3590 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3590>`_)
- ``tahoe invite`` will now read share encoding/placement configuration values from a Tahoe client node configuration file if they are not given on the command line, instead of raising an unhandled exception. (`#3650 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3650>`_)
- Fix regression where uploading files with non-ASCII names failed. (`#3738 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3738>`_)
- Fixed annoying UnicodeWarning message on Python 2 when running CLI tools. (`#3739 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3739>`_)
- Fixed bug where share corruption events were not logged on storage servers running on Windows. (`#3779 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3779>`_)


Dependency/Installation Changes
-------------------------------

- Tahoe-LAFS now requires Twisted 19.10.0 or newer.  As a result, it now has a transitive dependency on bcrypt. (`#1549 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1549>`_)
- Debian 8 support has been replaced with Debian 10 support. (`#3326 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3326>`_)
- Tahoe-LAFS no longer depends on Nevow. (`#3433 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3433>`_)
- Tahoe-LAFS now requires the `netifaces` Python package and no longer requires the external `ip`, `ifconfig`, or `route.exe` executables. (`#3486 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3486>`_)
- The Tahoe-LAFS project no longer commits to maintaining binary packages for all dependencies at <https://tahoe-lafs.org/deps>.  Please use PyPI instead. (`#3497 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3497>`_)
- Tahoe-LAFS now uses a forked version of txi2p (named txi2p-tahoe) with Python 3 support. (`#3633 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3633>`_)
- The Nix package now includes correct version information. (`#3712 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3712>`_)
- Use netifaces 0.11.0 wheel package from PyPI.org if you use 64-bit Python 2.7 on Windows.  VCPython27 downloads are no longer available at Microsoft's website, which has made building Python 2.7 wheel packages of Python libraries with C extensions (such as netifaces) on Windows difficult. (`#3733 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3733>`_)


Configuration Changes
---------------------

- The ``[client]introducer.furl`` configuration item is now deprecated in favor of the ``private/introducers.yaml`` file. (`#3504 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3504>`_)


Documentation Changes
---------------------

- Documentation now has its own towncrier category. (`#3664 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3664>`_)
- `tox -e docs` will treat warnings about docs as errors. (`#3666 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3666>`_)
- The visibility of the Tahoe-LAFS logo has been improved for "dark" themed viewing. (`#3677 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3677>`_)
- A cheatsheet-style document for contributors was created at CONTRIBUTORS.rst (`#3682 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3682>`_)
- Our IRC channel, #tahoe-lafs, has been moved to irc.libera.chat. (`#3721 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3721>`_)
- Tahoe-LAFS project is now registered with Libera.Chat IRC network. (`#3726 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3726>`_)
- Rewriting the installation guide for Tahoe-LAFS. (`#3747 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3747>`_)
- Documentation and installation links in the README have been fixed. (`#3749 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3749>`_)
- The Great Black Swamp proposed specification now includes sample interactions to demonstrate expected usage patterns. (`#3764 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3764>`_)
- The Great Black Swamp proposed specification now includes a glossary. (`#3765 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3765>`_)
- The Great Black Swamp specification now allows parallel upload of immutable share data. (`#3769 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3769>`_)
- There is now a specification for the scheme which Tahoe-LAFS storage clients use to derive their lease renewal secrets. (`#3774 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3774>`_)
- The Great Black Swamp proposed specification now has a simplified interface for reading data from immutable shares. (`#3777 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3777>`_)
- tahoe-dev mailing list is now at tahoe-dev@lists.tahoe-lafs.org. (`#3782 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3782>`_)
- The Great Black Swamp specification now describes the required authorization scheme. (`#3785 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3785>`_)
- The "Great Black Swamp" proposed specification has been expanded to include two lease management APIs. (`#3037 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3037>`_)
- The specification section of the Tahoe-LAFS documentation now includes explicit discussion of the security properties of Foolscap "fURLs" on which it depends. (`#3503 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3503>`_)
- The README, revised by Viktoriia with feedback from the team, is now more focused on the developer community and provides more information about Tahoe-LAFS, why it's important, and how someone can use it or start contributing to it. (`#3545 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3545>`_)
- The "Great Black Swamp" proposed specification has been changed use ``v=1`` as the URL version identifier. (`#3644 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3644>`_)
- You can run `make livehtml` in docs directory to invoke sphinx-autobuild. (`#3663 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3663>`_)


Removed Features
----------------

- Announcements delivered through the introducer system are no longer automatically annotated with copious information about the Tahoe-LAFS software version nor the versions of its dependencies. (`#3518 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3518>`_)
- The stats gatherer, broken since at least Tahoe-LAFS 1.13.0, has been removed.  The ``[client]stats_gatherer.furl`` configuration item in ``tahoe.cfg`` is no longer allowed.  The Tahoe-LAFS project recommends using a third-party metrics aggregation tool instead. (`#3549 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3549>`_)
- The deprecated ``tahoe`` start, restart, stop, and daemonize sub-commands have been removed. (`#3550 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3550>`_)
- FTP is no longer supported by Tahoe-LAFS. Please use the SFTP support instead. (`#3583 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3583>`_)
- Removed support for the Account Server frontend authentication type. (`#3652 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3652>`_)


Other Changes
-------------

- Refactored test_introducer in web tests to use custom base test cases (`#3757 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3757>`_)


Misc/Other
----------

- `#2928 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2928>`_, `#3283 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3283>`_, `#3314 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3314>`_, `#3384 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3384>`_, `#3385 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3385>`_, `#3390 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3390>`_, `#3404 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3404>`_, `#3428 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3428>`_, `#3432 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3432>`_, `#3434 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3434>`_, `#3435 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3435>`_, `#3454 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3454>`_, `#3459 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3459>`_, `#3460 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3460>`_, `#3465 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3465>`_, `#3466 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3466>`_, `#3467 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3467>`_, `#3468 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3468>`_, `#3470 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3470>`_, `#3471 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3471>`_, `#3472 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3472>`_, `#3473 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3473>`_, `#3474 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3474>`_, `#3475 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3475>`_, `#3477 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3477>`_, `#3478 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3478>`_, `#3479 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3479>`_, `#3481 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3481>`_, `#3482 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3482>`_, `#3483 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3483>`_, `#3485 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3485>`_, `#3488 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3488>`_, `#3490 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3490>`_, `#3491 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3491>`_, `#3492 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3492>`_, `#3493 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3493>`_, `#3496 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3496>`_, `#3499 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3499>`_, `#3500 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3500>`_, `#3501 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3501>`_, `#3502 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3502>`_, `#3511 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3511>`_, `#3513 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3513>`_, `#3514 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3514>`_, `#3515 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3515>`_, `#3517 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3517>`_, `#3520 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3520>`_, `#3521 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3521>`_, `#3522 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3522>`_, `#3523 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3523>`_, `#3524 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3524>`_, `#3528 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3528>`_, `#3529 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3529>`_, `#3532 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3532>`_, `#3533 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3533>`_, `#3534 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3534>`_, `#3536 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3536>`_, `#3537 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3537>`_, `#3542 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3542>`_, `#3544 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3544>`_, `#3546 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3546>`_, `#3547 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3547>`_, `#3551 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3551>`_, `#3552 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3552>`_, `#3553 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3553>`_, `#3555 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3555>`_, `#3557 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3557>`_, `#3558 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3558>`_, `#3560 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3560>`_, `#3563 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3563>`_, `#3564 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3564>`_, `#3565 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3565>`_, `#3566 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3566>`_, `#3567 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3567>`_, `#3568 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3568>`_, `#3572 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3572>`_, `#3574 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3574>`_, `#3575 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3575>`_, `#3576 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3576>`_, `#3577 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3577>`_, `#3578 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3578>`_, `#3579 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3579>`_, `#3580 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3580>`_, `#3582 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3582>`_, `#3587 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3587>`_, `#3588 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3588>`_, `#3589 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3589>`_, `#3591 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3591>`_, `#3592 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3592>`_, `#3593 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3593>`_, `#3594 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3594>`_, `#3595 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3595>`_, `#3596 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3596>`_, `#3599 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3599>`_, `#3600 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3600>`_, `#3603 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3603>`_, `#3605 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3605>`_, `#3606 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3606>`_, `#3607 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3607>`_, `#3608 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3608>`_, `#3611 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3611>`_, `#3612 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3612>`_, `#3613 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3613>`_, `#3615 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3615>`_, `#3616 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3616>`_, `#3617 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3617>`_, `#3618 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3618>`_, `#3619 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3619>`_, `#3620 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3620>`_, `#3621 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3621>`_, `#3623 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3623>`_, `#3624 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3624>`_, `#3625 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3625>`_, `#3626 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3626>`_, `#3628 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3628>`_, `#3630 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3630>`_, `#3631 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3631>`_, `#3632 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3632>`_, `#3634 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3634>`_, `#3635 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3635>`_, `#3637 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3637>`_, `#3638 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3638>`_, `#3640 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3640>`_, `#3642 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3642>`_, `#3645 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3645>`_, `#3646 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3646>`_, `#3647 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3647>`_, `#3648 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3648>`_, `#3649 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3649>`_, `#3651 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3651>`_, `#3653 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3653>`_, `#3654 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3654>`_, `#3655 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3655>`_, `#3656 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3656>`_, `#3657 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3657>`_, `#3658 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3658>`_, `#3662 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3662>`_, `#3667 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3667>`_, `#3669 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3669>`_, `#3670 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3670>`_, `#3671 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3671>`_, `#3672 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3672>`_, `#3674 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3674>`_, `#3675 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3675>`_, `#3676 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3676>`_, `#3678 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3678>`_, `#3679 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3679>`_, `#3681 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3681>`_, `#3683 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3683>`_, `#3686 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3686>`_, `#3687 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3687>`_, `#3691 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3691>`_, `#3692 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3692>`_, `#3699 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3699>`_, `#3700 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3700>`_, `#3701 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3701>`_, `#3702 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3702>`_, `#3703 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3703>`_, `#3704 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3704>`_, `#3705 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3705>`_, `#3707 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3707>`_, `#3708 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3708>`_, `#3709 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3709>`_, `#3711 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3711>`_, `#3713 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3713>`_, `#3714 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3714>`_, `#3715 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3715>`_, `#3717 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3717>`_, `#3718 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3718>`_, `#3722 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3722>`_, `#3723 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3723>`_, `#3727 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3727>`_, `#3728 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3728>`_, `#3729 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3729>`_, `#3730 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3730>`_, `#3731 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3731>`_, `#3732 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3732>`_, `#3734 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3734>`_, `#3735 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3735>`_, `#3736 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3736>`_, `#3741 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3741>`_, `#3743 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3743>`_, `#3744 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3744>`_, `#3745 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3745>`_, `#3746 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3746>`_, `#3751 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3751>`_, `#3759 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3759>`_, `#3760 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3760>`_, `#3763 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3763>`_, `#3773 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3773>`_, `#3781 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3781>`_


Release 1.15.1
''''''''''''''

Misc/Other
----------

- `#3469 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3469>`_, `#3608 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3608>`_


Release 1.15.0 (2020-10-13)
'''''''''''''''''''''''''''

Features
--------

- PyPy is now a supported platform. (`#1792 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1792>`_)
- allmydata.testing.web, a new module, now offers a supported Python API for testing Tahoe-LAFS web API clients. (`#3317 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3317>`_)


Bug Fixes
---------

- Make directory page links work. (`#3312 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3312>`_)
- Use last known revision of Chutney that is known to work with Python 2 for Tor integration tests. (`#3348 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3348>`_)
- Mutable files now use RSA exponent 65537 (`#3349 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3349>`_)


Dependency/Installation Changes
-------------------------------

- Tahoe-LAFS now supports CentOS 8 and no longer supports CentOS 7. (`#3296 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3296>`_)
- Tahoe-LAFS now supports Ubuntu 20.04. (`#3328 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3328>`_)


Removed Features
----------------

- The Magic Folder frontend has been split out into a stand-alone project.  The functionality is no longer part of Tahoe-LAFS itself.  Learn more at <https://github.com/LeastAuthority/magic-folder>. (`#3284 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3284>`_)
- Slackware 14.2 is no longer a Tahoe-LAFS supported platform. (`#3323 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3323>`_)


Other Changes
-------------

- The Tahoe-LAFS project has adopted a formal code of conduct. (`#2755 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2755>`_)
-  (`#3263 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3263>`_, `#3324 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3324>`_)
- The "coverage" tox environment has been replaced by the "py27-coverage" and "py36-coverage" environments. (`#3355 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3355>`_)


Misc/Other
----------

- `#3247 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3247>`_, `#3254 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3254>`_, `#3277 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3277>`_, `#3278 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3278>`_, `#3287 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3287>`_, `#3288 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3288>`_, `#3289 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3289>`_, `#3290 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3290>`_, `#3291 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3291>`_, `#3292 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3292>`_, `#3293 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3293>`_, `#3294 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3294>`_, `#3297 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3297>`_, `#3298 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3298>`_, `#3299 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3299>`_, `#3300 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3300>`_, `#3302 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3302>`_, `#3303 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3303>`_, `#3304 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3304>`_, `#3305 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3305>`_, `#3306 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3306>`_, `#3308 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3308>`_, `#3309 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3309>`_, `#3313 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3313>`_, `#3315 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3315>`_, `#3316 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3316>`_, `#3320 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3320>`_, `#3325 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3325>`_, `#3326 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3326>`_, `#3329 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3329>`_, `#3330 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3330>`_, `#3331 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3331>`_, `#3332 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3332>`_, `#3333 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3333>`_, `#3334 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3334>`_, `#3335 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3335>`_, `#3336 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3336>`_, `#3338 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3338>`_, `#3339 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3339>`_, `#3340 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3340>`_, `#3341 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3341>`_, `#3342 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3342>`_, `#3343 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3343>`_, `#3344 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3344>`_, `#3346 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3346>`_, `#3351 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3351>`_, `#3353 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3353>`_, `#3354 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3354>`_, `#3356 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3356>`_, `#3357 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3357>`_, `#3358 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3358>`_, `#3359 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3359>`_, `#3361 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3361>`_, `#3364 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3364>`_, `#3365 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3365>`_, `#3366 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3366>`_, `#3367 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3367>`_, `#3368 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3368>`_, `#3370 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3370>`_, `#3372 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3372>`_, `#3373 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3373>`_, `#3374 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3374>`_, `#3375 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3375>`_, `#3376 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3376>`_, `#3377 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3377>`_, `#3378 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3378>`_, `#3380 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3380>`_, `#3381 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3381>`_, `#3382 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3382>`_, `#3383 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3383>`_, `#3386 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3386>`_, `#3387 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3387>`_, `#3388 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3388>`_, `#3389 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3389>`_, `#3391 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3391>`_, `#3392 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3392>`_, `#3393 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3393>`_, `#3394 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3394>`_, `#3395 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3395>`_, `#3396 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3396>`_, `#3397 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3397>`_, `#3398 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3398>`_, `#3401 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3401>`_, `#3403 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3403>`_, `#3406 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3406>`_, `#3408 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3408>`_, `#3409 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3409>`_, `#3411 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3411>`_, `#3415 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3415>`_, `#3416 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3416>`_, `#3417 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3417>`_, `#3421 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3421>`_, `#3422 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3422>`_, `#3423 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3423>`_, `#3424 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3424>`_, `#3425 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3425>`_, `#3426 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3426>`_, `#3427 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3427>`_, `#3429 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3429>`_, `#3430 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3430>`_, `#3431 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3431>`_, `#3436 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3436>`_, `#3437 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3437>`_, `#3438 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3438>`_, `#3439 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3439>`_, `#3440 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3440>`_, `#3442 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3442>`_, `#3443 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3443>`_, `#3446 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3446>`_, `#3448 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3448>`_, `#3449 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3449>`_, `#3450 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3450>`_, `#3451 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3451>`_, `#3452 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3452>`_, `#3453 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3453>`_, `#3455 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3455>`_, `#3456 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3456>`_, `#3458 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3458>`_, `#3462 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3462>`_, `#3463 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3463>`_, `#3464 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3464>`_


Release 1.14.0 (2020-03-11)
'''''''''''''''''''''''''''

Features
--------

- Magic-Folders are now supported on macOS. (`#1432 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1432>`_)
- Add a "tox -e draftnews" which runs towncrier in draft mode (`#2942 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2942>`_)
- Fedora 29 is now tested as part of the project's continuous integration system. (`#2955 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2955>`_)
- The Magic-Folder frontend now emits structured, causal logs.  This makes it easier for developers to make sense of its behavior and for users to submit useful debugging information alongside problem reports. (`#2972 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2972>`_)
- The `tahoe` CLI now accepts arguments for configuring structured logging messages which Tahoe-LAFS is being converted to emit.  This change does not introduce any new defaults for on-filesystem logging. (`#2975 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2975>`_)
- The web API now publishes streaming Eliot logs via a token-protected WebSocket at /private/logs/v1. (`#3006 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3006>`_)
- End-to-end in-memory tests for websocket features (`#3041 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3041>`_)
- allmydata.interfaces.IFoolscapStoragePlugin has been introduced, an extension point for customizing the storage protocol. (`#3049 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3049>`_)
- Static storage server "announcements" in ``private/servers.yaml`` are now individually logged and ignored if they cannot be interpreted. (`#3051 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3051>`_)
- Storage servers can now be configured to load plugins for allmydata.interfaces.IFoolscapStoragePlugin and offer them to clients. (`#3053 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3053>`_)
- Storage clients can now be configured to load plugins for allmydata.interfaces.IFoolscapStoragePlugin and use them to negotiate with servers. (`#3054 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3054>`_)
- The [storage] configuration section now accepts a boolean *anonymous* item to enable or disable anonymous storage access.  The default behavior remains unchanged. (`#3184 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3184>`_)
- Enable the helper when creating a node with `tahoe create-node --helper` (`#3235 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3235>`_)


Bug Fixes
---------

- refactor initialization code to be more async-friendly (`#2870 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2870>`_)
- Configuration-checking code wasn't being called due to indenting (`#2935 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2935>`_)
- refactor configuration handling out of Node into _Config (`#2936 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2936>`_)
- "tox -e codechecks" no longer dirties the working tree. (`#2941 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2941>`_)
- Updated the Tor release key, used by the integration tests. (`#2944 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2944>`_)
- `tahoe backup` no longer fails with an unhandled exception when it encounters a special file (device, fifo) in the backup source. (`#2950 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2950>`_)
- Magic-Folders now creates spurious conflict files in fewer cases.  In particular, if files are added to the folder while a client is offline, that client will not create conflict files for all those new files when it starts up. (`#2965 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2965>`_)
- The confusing and misplaced sub-command group headings in `tahoe --help` output have been removed. (`#2976 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2976>`_)
- The Magic-Folder frontend is now more responsive to subtree changes on Windows. (`#2997 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2997>`_)
- remove ancient bundled jquery and d3, and the "dowload timeline" feature they support (`#3228 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3228>`_)


Dependency/Installation Changes
-------------------------------

- Tahoe-LAFS no longer makes start-up time assertions about the versions of its dependencies.  It is the responsibility of the administrator of the installation to ensure the correct version of dependencies are supplied. (`#2749 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2749>`_)
- Tahoe-LAFS now depends on Twisted 16.6 or newer. (`#2957 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2957>`_)


Removed Features
----------------

- "tahoe rm", an old alias for "tahoe unlink", has been removed. (`#1827 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1827>`_)
- The direct dependencies on pyutil and zbase32 have been removed. (`#2098 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2098>`_)
- Untested and unmaintained code for running Tahoe-LAFS as a Windows service has been removed. (`#2239 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2239>`_)
- The redundant "pypywin32" dependency has been removed. (`#2392 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2392>`_)
- Fedora 27 is no longer tested as part of the project's continuous integration system. (`#2955 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2955>`_)
- "tahoe start", "tahoe daemonize", "tahoe restart", and "tahoe stop" are now deprecated in favor of using "tahoe run", possibly with a third-party process manager. (`#3273 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3273>`_)


Other Changes
-------------

- Tahoe-LAFS now tests for PyPy compatibility on CI. (`#2479 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2479>`_)
- Tahoe-LAFS now requires Twisted 18.4.0 or newer. (`#2771 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2771>`_)
- Tahoe-LAFS now uses towncrier to maintain the NEWS file. (`#2908 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2908>`_)
- The release process document has been updated. (`#2920 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2920>`_)
- allmydata.test.test_system.SystemTest is now more reliable with respect to bound address collisions. (`#2933 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2933>`_)
- The Tox configuration has been fixed to work around a problem on Windows CI. (`#2956 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2956>`_)
- The PyInstaller CI job now works around a pip/pyinstaller incompatibility. (`#2958 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2958>`_)
- Some CI jobs for integration tests have been moved from TravisCI to CircleCI. (`#2959 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2959>`_)
- Several warnings from a new release of pyflakes have been fixed. (`#2960 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2960>`_)
- Some Slackware 14.2 continuous integration problems have been resolved. (`#2961 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2961>`_)
- Some macOS continuous integration failures have been fixed. (`#2962 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2962>`_)
- The NoNetworkGrid implementation has been somewhat improved. (`#2966 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2966>`_)
- A bug in the test suite for the create-alias command has been fixed. (`#2967 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2967>`_)
- The integration test suite has been updated to use pytest-twisted instead of deprecated pytest APIs. (`#2968 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2968>`_)
- The magic-folder integration test suite now performs more aggressive cleanup of the processes it launches. (`#2969 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2969>`_)
- The integration tests now correctly document the `--keep-tempdir` option. (`#2970 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2970>`_)
- A misuse of super() in the integration tests has been fixed. (`#2971 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2971>`_)
- Several utilities to facilitate the use of the Eliot causal logging library have been introduced. (`#2973 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2973>`_)
- The Windows CI configuration has been tweaked. (`#2974 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2974>`_)
- The Magic-Folder frontend has had additional logging improvements. (`#2977 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2977>`_)
-  (`#2981 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2981>`_, `#2982 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2982>`_)
- Added a simple sytax checker so that once a file has reached python3 compatibility, it will not regress. (`#3001 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3001>`_)
- Converted all uses of the print statement to the print function in the ./misc/ directory. (`#3002 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3002>`_)
- The contributor guidelines are now linked from the GitHub pull request creation page. (`#3003 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3003>`_)
- Updated the testing code to use the print function instead of the print statement. (`#3008 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3008>`_)
- Replaced print statement with print fuction for all tahoe_* scripts. (`#3009 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3009>`_)
- Replaced all remaining instances of the print statement with the print function. (`#3010 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3010>`_)
- Replace StringIO imports with six.moves. (`#3011 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3011>`_)
- Updated all Python files to use PEP-3110 exception syntax for Python3 compatibility. (`#3013 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3013>`_)
- Update raise syntax for Python3 compatibility. (`#3014 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3014>`_)
- Updated instances of octal literals to use the format 0o123 for Python3 compatibility. (`#3015 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3015>`_)
- allmydata.test.no_network, allmydata.test.test_system, and allmydata.test.web.test_introducer are now more reliable with respect to bound address collisions. (`#3016 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3016>`_)
- Removed tuple unpacking from function and lambda definitions for Python3 compatibility. (`#3019 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3019>`_)
- Updated Python2 long numeric literals for Python3 compatibility. (`#3020 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3020>`_)
- CircleCI jobs are now faster as a result of pre-building configured Docker images for the CI jobs. (`#3024 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3024>`_)
- Removed used of backticks for "repr" for Python3 compatibility. (`#3027 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3027>`_)
- Updated string literal syntax for Python3 compatibility. (`#3028 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3028>`_)
- Updated CI to enforce Python3 syntax for entire repo. (`#3030 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3030>`_)
- Replaced pycryptopp with cryptography. (`#3031 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3031>`_)
- All old-style classes ported to new-style. (`#3042 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3042>`_)
- Whitelisted "/bin/mv" as command for codechecks performed by tox. This fixes a current warning and prevents future errors (for tox 4). (`#3043 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3043>`_)
- Progress towards Python 3 compatibility is now visible at <https://tahoe-lafs.github.io/tahoe-depgraph/>. (`#3152 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3152>`_)
- Collect coverage information from integration tests (`#3234 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3234>`_)
- NixOS is now a supported Tahoe-LAFS platform. (`#3266 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3266>`_)


Misc/Other
----------

- `#1893 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1893>`_, `#2266 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2266>`_, `#2283 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2283>`_, `#2766 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2766>`_, `#2980 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2980>`_, `#2985 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2985>`_, `#2986 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2986>`_, `#2987 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2987>`_, `#2988 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2988>`_, `#2989 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2989>`_, `#2990 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2990>`_, `#2991 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2991>`_, `#2992 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2992>`_, `#2995 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2995>`_, `#3000 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3000>`_, `#3004 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3004>`_, `#3005 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3005>`_, `#3007 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3007>`_, `#3012 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3012>`_, `#3017 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3017>`_, `#3021 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3021>`_, `#3023 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3023>`_, `#3025 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3025>`_, `#3026 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3026>`_, `#3029 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3029>`_, `#3036 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3036>`_, `#3038 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3038>`_, `#3048 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3048>`_, `#3086 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3086>`_, `#3097 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3097>`_, `#3111 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3111>`_, `#3118 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3118>`_, `#3119 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3119>`_, `#3227 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3227>`_, `#3229 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3229>`_, `#3232 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3232>`_, `#3233 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3233>`_, `#3237 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3237>`_, `#3238 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3238>`_, `#3239 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3239>`_, `#3240 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3240>`_, `#3242 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3242>`_, `#3243 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3243>`_, `#3245 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3245>`_, `#3246 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3246>`_, `#3248 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3248>`_, `#3250 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3250>`_, `#3252 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3252>`_, `#3255 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3255>`_, `#3256 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3256>`_, `#3259 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3259>`_, `#3261 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3261>`_, `#3262 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3262>`_, `#3263 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3263>`_, `#3264 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3264>`_, `#3265 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3265>`_, `#3267 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3267>`_, `#3268 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3268>`_, `#3271 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3271>`_, `#3272 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3272>`_, `#3274 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3274>`_, `#3275 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3275>`_, `#3276 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3276>`_, `#3279 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3279>`_, `#3281 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3281>`_, `#3282 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3282>`_, `#3285 <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3285>`_


Release 1.13.0 (05-August-2018)
'''''''''''''''''''''''''''''''

New Features
------------

The ``tahoe list-aliases`` command gained the ``--readonly-uri``
option in `PR400`_, which lists read-only capabilities (the default
shows read/write capabilities if available). This command also gained
a ``--json`` option in `PR452`_, providing machine-readable output.

A new command ``tahoe status`` is added, showing some statistics and
currently active operations (similar to the ``/status`` page in the
Web UI). See also `PR502`_.

Immutable uploads now use the "servers of happiness" algorithm for
uploading shares. This means better placement of shares on available
servers. See `PR416`_.

To join a new client to a grid, the command ``tahoe invite`` was
added. This uses `magic wormhole`_ to connect two computers and
exchange the required information to start the client. The "client
side" of this command is the also new option ``tahoe
create-client --join=``. Together, this provides a way to provision a
new client without having to securely transmit the fURL and other
details. `PR418`_

``tahoe backup`` now reports progress. `PR474`_

The ``tub.port=`` option can now accept ``listen:i2p`` or
``listen:tor`` options to use popular anonymity networks with storage
servers. See `PR437`_

The place where storage servers put shares (the "storage path") is now
configurable (`PR472`_).

A PyInstaller-based build is now available (`PR421`_). A "Docker
compose" setup for development purposes is now available (`PR445`_).

There is now a recommended workflow for Zcash-based donations to support
storage server operators (`PR506`_).

Bug Fixes in Core
-----------------

Some bugs with pidfile handling were fixed (`PR440`_ and `PR450`_)
meaning invalid pidfiles are now deleted. Error-messages related to
``tahoe.cfg`` now include the full path to the file. `PR501`_ fixes
"address already in use" test failures. `PR502`_ fixes ticket #2926
("tahoe status" failures). `PR487`_ fixes ticket #1455 (setting
``X-Frame-Options: DENY``)


Web UI Changes
--------------

We set the "Referrer-Policy: no-referrer" header on all requests. The
Welcome page now understands the JSON option (`PR430`_) and OPTIONS
requests are handled (`PR447`_).


Magic Folder Changes
--------------------

Multiple magic-folders in a single Tahoe client are now
supported. Bugs with ``.backup`` files have been fixed, meaning
spurious ``.backup`` files will be produced less often (`PR448`_,
`PR475`_). Handling of default umask on new magic-folder files is
fixed in `PR458`_. The user mtime value is now correctly preserved
(`PR457`_).

A bug in ``tahoe magic-folder status`` causing active operations to
sometimes not show up is fixed (`PR461`_). If a directory is missing,
it is created (`PR492`_).


Raw Pull Requests
-----------------

In total, 50 Pull Requests were merged for this release, including
contributions of code or review from 15 different GitHub users. Thanks
everyone! A complete list of these PRs and contributions:

`PR380`_: `daira`_
`PR400`_: `meejah`_ (with `warner`_)
`PR403`_: `meejah`_
`PR405`_: `meejah`_ (with `warner`_)
`PR406`_: `meejah`_ (with `warner`_)
`PR407`_: `david415`_ (with `meejah`_, `warner`_)
`PR409`_: `str4d`_ (with `warner`_)
`PR410`_: `tpltnt`_ (with `warner`_)
`PR411`_: `tpltnt`_ (with `warner`_, `meejah`_)
`PR412`_: `tpltnt`_ (with `warner`_)
`PR414`_: `tpltnt`_ (with `meejah`_, `warner`_)
`PR416`_: `david415`_, `meejah`_, `markberger`_, `warner`_
`PR417`_: `meejah`_ (with `pataquets`_, `warner`_)
`PR418`_: `meejah`_ (with `crwood`_, `exarkun`_, `warner`_)
`PR419`_: `tpltnt`_ (with `warner`_)
`PR420`_: `ValdikSS`_ (with `warner`_)
`PR421`_: `crwood`_ (with `meejah`_, `warner`_)
`PR423`_: `warner`_
`PR428`_: `warner`_
`PR429`_: `exarkun`_ (with `warner`_)
`PR430`_: `david415`_, `exarkun`_ (with `warner`_)
`PR432`_: `exarkun`_ (with `meejah`_)
`PR433`_: `exarkun`_ (with `warner`_)
`PR434`_: `exarkun`_ (with `warner`_)
`PR437`_: `warner`_
`PR438`_: `warner`_ (with `meejah`_)
`PR440`_: `exarkun`_, `lpirl`_ (with `meejah`_)
`PR444`_: `AnBuKu`_ (with `warner`_)
`PR445`_: `bookchin`_ (with `warner`_)
`PR447`_: `meejah`_ (with `tpltnt`_, `meejah`_)
`PR448`_: `meejah`_ (with `warner`_)
`PR450`_: `exarkun`_, `meejah`_, `lpirl`_
`PR452`_: `meejah`_ (with `tpltnt`_)
`PR453`_: `meejah`_
`PR454`_: `meejah`_ (with `tpltnt`_, `meejah`_, `warner`_)
`PR455`_: `tpltnt`_ (with `meejah`_)
`PR456`_: `meejah`_ (with `meejah`_)
`PR457`_: `meejah`_ (with `crwood`_, `tpltnt`_)
`PR458`_: `meejah`_ (with `tpltnt`_)
`PR460`_: `tpltnt`_ (with `exarkun`_, `meejah`_)
`PR462`_: `meejah`_ (with `crwood`_)
`PR464`_: `meejah`_
`PR470`_: `meejah`_ (with `exarkun`_, `tpltnt`_, `warner`_)
`PR472`_: `exarkun`_, `meskio`_
`PR474`_: `exarkun`_
`PR475`_: `meejah`_ (with `exarkun`_)
`PR482`_: `crwood`_ (with `warner`_)
`PR485`_: `warner`_
`PR486`_: `exarkun`_ (with `warner`_)
`PR487`_: `exarkun`_ (with `tpltnt`_)
`PR489`_: `exarkun`_
`PR490`_: `exarkun`_
`PR491`_: `exarkun`_ (with `meejah`_)
`PR492`_: `exarkun`_ (with `meejah`_, `tpltnt`_)
`PR493`_: `exarkun`_ (with `meejah`_)
`PR494`_: `exarkun`_ (with `meejah`_)
`PR497`_: `meejah`_ (with `multikatt`_, `exarkun`_)
`PR499`_: `exarkun`_ (with `meejah`_)
`PR501`_: `exarkun`_ (with `meejah`_)
`PR502`_: `exarkun`_ (with `meejah`_)
`PR506`_: `exarkun`_ (with `crwood`_, `nejucomo`_)


Developer and Internal Changes
------------------------------

People hacking on Tahoe-LAFS code will be interested in some internal
improvements which shouldn't have any user-visible effects:

* internal: skip some unicode tests on non-unicode platforms #2912
* internal: tox: pre-install Incremental to workaround setuptools bug #2913
* internal: fix PyInstaller builds `PR482`_
* internal: use @implementer instead of implements `PR406`_
* internal: improve happiness integration test #2895 `PR432`_
* web internal: refactor response-format (?t=) logic #2893 `PR429`_
* internal: fix pyflakes issues #2898 `PR434`_
* internal: setup.py use find_packages #2897 `PR433`_
* internal: ValueOrderedDict fixes #2891
* internal: remove unnused NumDict #2891 `PR438`_
* internal: setup.py use python_requires= so tox3 works #2876
* internal: rewrite tahoe stop/start/daemonize refs #1148 #275 #1121 #1377 #2149 #719 `PR417`_
* internal: add docs links to RFCs/etc `PR456`_
* internal: magic-folder test improvement `PR453`_
* internal: pytest changes `PR462`_
* internal: upload appveyor generated wheels as artifacts #2903
* internal: fix tox-vs-setuptools-upgrade #2910
* deps: require txi2p>=0.3.2 to work around TLS who-is-client issue #2861 `PR409`_
* deps: now need libyaml-dev from system before build `PR420`_
* deps: twisted>=16.4.0 for "python -m twisted.trial" `PR454`_
* deps: pin pypiwin32 to 219 until upstream bug resolved `PR464`_
* deps: setuptools >=28.8.0 for something `PR470`_
* deps: use stdlib "json" instead of external "simplejson" #2766 `PR405`_
* complain more loudly in setup.py under py3 `PR414`_
* rename "filesystem" to "file store" #2345 `PR380`_
* replace deprecated twisted.web.client with treq #2857 `PR428`_
* improve/stablize some test coverage #2891
* TODO: can we remove this now? pypiwin32 is now at 223
* use secure mkstemp() `PR460`_
* test "tahoe list-aliases --readonly-uri" #2863 `PR403`_
* #455: remove outdated comment
* `PR407`_ fix stopService calls
* `PR410`_ explicit python2.7 virtualenv
* `PR419`_ fix list of supported OSes
* `PR423`_ switch travis to a supported Ubuntu
* deps: no longer declare a PyCrypto dependency (actual use vanished long ago) `PR514`_



.. _PR380: https://github.com/tahoe-lafs/tahoe-lafs/pull/380
.. _PR400: https://github.com/tahoe-lafs/tahoe-lafs/pull/400
.. _PR403: https://github.com/tahoe-lafs/tahoe-lafs/pull/403
.. _PR405: https://github.com/tahoe-lafs/tahoe-lafs/pull/405
.. _PR406: https://github.com/tahoe-lafs/tahoe-lafs/pull/406
.. _PR407: https://github.com/tahoe-lafs/tahoe-lafs/pull/407
.. _PR409: https://github.com/tahoe-lafs/tahoe-lafs/pull/409
.. _PR410: https://github.com/tahoe-lafs/tahoe-lafs/pull/410
.. _PR412: https://github.com/tahoe-lafs/tahoe-lafs/pull/412
.. _PR414: https://github.com/tahoe-lafs/tahoe-lafs/pull/414
.. _PR416: https://github.com/tahoe-lafs/tahoe-lafs/pull/416
.. _PR417: https://github.com/tahoe-lafs/tahoe-lafs/pull/417
.. _PR418: https://github.com/tahoe-lafs/tahoe-lafs/pull/418
.. _PR419: https://github.com/tahoe-lafs/tahoe-lafs/pull/419
.. _PR420: https://github.com/tahoe-lafs/tahoe-lafs/pull/420
.. _PR421: https://github.com/tahoe-lafs/tahoe-lafs/pull/421
.. _PR423: https://github.com/tahoe-lafs/tahoe-lafs/pull/423
.. _PR428: https://github.com/tahoe-lafs/tahoe-lafs/pull/428
.. _PR429: https://github.com/tahoe-lafs/tahoe-lafs/pull/429
.. _PR430: https://github.com/tahoe-lafs/tahoe-lafs/pull/430
.. _PR432: https://github.com/tahoe-lafs/tahoe-lafs/pull/432
.. _PR433: https://github.com/tahoe-lafs/tahoe-lafs/pull/433
.. _PR434: https://github.com/tahoe-lafs/tahoe-lafs/pull/434
.. _PR437: https://github.com/tahoe-lafs/tahoe-lafs/pull/437
.. _PR438: https://github.com/tahoe-lafs/tahoe-lafs/pull/438
.. _PR440: https://github.com/tahoe-lafs/tahoe-lafs/pull/440
.. _PR444: https://github.com/tahoe-lafs/tahoe-lafs/pull/444
.. _PR445: https://github.com/tahoe-lafs/tahoe-lafs/pull/445
.. _PR447: https://github.com/tahoe-lafs/tahoe-lafs/pull/447
.. _PR448: https://github.com/tahoe-lafs/tahoe-lafs/pull/448
.. _PR450: https://github.com/tahoe-lafs/tahoe-lafs/pull/450
.. _PR452: https://github.com/tahoe-lafs/tahoe-lafs/pull/452
.. _PR453: https://github.com/tahoe-lafs/tahoe-lafs/pull/453
.. _PR454: https://github.com/tahoe-lafs/tahoe-lafs/pull/454
.. _PR456: https://github.com/tahoe-lafs/tahoe-lafs/pull/456
.. _PR457: https://github.com/tahoe-lafs/tahoe-lafs/pull/457
.. _PR458: https://github.com/tahoe-lafs/tahoe-lafs/pull/458
.. _PR460: https://github.com/tahoe-lafs/tahoe-lafs/pull/460
.. _PR462: https://github.com/tahoe-lafs/tahoe-lafs/pull/462
.. _PR464: https://github.com/tahoe-lafs/tahoe-lafs/pull/464
.. _PR470: https://github.com/tahoe-lafs/tahoe-lafs/pull/470
.. _PR472: https://github.com/tahoe-lafs/tahoe-lafs/pull/472
.. _PR474: https://github.com/tahoe-lafs/tahoe-lafs/pull/474
.. _PR482: https://github.com/tahoe-lafs/tahoe-lafs/pull/482
.. _PR502: https://github.com/tahoe-lafs/tahoe-lafs/pull/502
.. _PR506: https://github.com/tahoe-lafs/tahoe-lafs/pull/506
.. _PR514: https://github.com/tahoe-lafs/tahoe-lafs/pull/514
.. _AnBuKu: https://github.com/AnBuKu
.. _ValdikSS: https://github.com/ValdikSS
.. _bookchin: https://github.com/bookchin
.. _crwood: https://github.com/crwood
.. _nejucomo: https://github.com/nejucomo
.. _daira: https://github.com/daira
.. _david415: https://github.com/david415
.. _exarkun: https://github.com/exarkun
.. _lpirl: https://github.com/lpirl
.. _markberger: https://github.com/markberger
.. _meejah: https://github.com/meejah
.. _meskio: https://github.com/meskio
.. _multikatt: https://github.com/multikatt
.. _pataquets: https://github.com/pataquets
.. _str4d: https://github.com/str4d
.. _tpltnt: https://github.com/tpltnt
.. _warner: https://github.com/warner




Release 1.12.1 (18-Jan-2017)
''''''''''''''''''''''''''''

This fixes a few small problems discovered just after 1.12.0 was released.

* ``introducers.yaml`` was entirely broken (due to a unicode-vs-ascii
  problem), and the documentation recommended an invalid syntax. Both have
  been fixed. (#2862)
* Creating a node with ``--hide-ip`` shouldn't set ``tcp = tor`` if txtorcon
  is unavailable. I2P-only systems should get ``tcp = disabled``. (#2860)
* As a result, we now require foolscap-0.12.6 .
* setup.py now creates identical wheels on win32 and unix. Previously wheels
  created on windows got an unconditional dependency upon ``pypiwin32``,
  making them uninstallable on unix. Now that dependency is marked as
  ``sys_platform=win32`` only. (#2763)

Some other small changes include:

* The deep-stats t=json response now includes an "api-version" field,
  currently set to 1. (#567)
* WUI Directory listings use ``rel=noreferrer`` to avoid leaking the dircap
  to the JS contents of the target file. (#151, #378)
* Remove the dependency on ``shutilwhich`` (#2856)


Release 1.12.0 (17-Dec-2016)
''''''''''''''''''''''''''''

New Features
------------

This release features improved Tor/I2P integration. It is now easy to::

* use Tor to hide your IP address during external network activity
* connect to Tor/I2P-based storage servers
* run an Introducer or a storage node as a Tor "onion service"

See docs/anonymity-configuration.rst for instructions and new node-creation
arguments (--hide-ip, --listen=tor), which include ways to use SOCKS servers
for outbound connections. Tor/I2P/Socks support requires extra python
libraries to be installed (e.g. 'pip install tahoe-lafs[tor]'), as well as
matching (non-python) daemons available on the host system. (tickets #517,
#2490, #2838)

Nodes can use multiple introducers by adding entries to a new
``private/introducers.yaml`` file, or stop using introduction entirely by
omitting the ``introducer.furl`` key from tahoe.cfg (introducerless clients
will need static servers configured to connect anywhere). Server
announcements are sent to all connected Introducers, and clients merge all
announcements they see, which can improve grid reliability. (#68)

In addition, nodes now cache the announcements they receive in a YAML file,
and use their cached information at startup until the Introducer connection
is re-established. This makes nodes more tolerant of Introducers that are
temporarily offline. Nodes admins can copy text from the cache into a new
``private/servers.yaml`` file to add "static servers", which augment/override
what the Introducer offers. This can modify aspects of the server, or use
servers that were never announced in the first place. (#2788)

Nodes now use a separate Foolscap "Tub" for each server connection, so
``servers.yaml`` can override the connection rules (Tor vs direct-TCP) for
each one independently. This offers a slight privacy improvement, but slows
down connections slightly (perhaps 75ms per server), and breaks an obscure
NAT-bypass trick which enabled storage servers to run behind NAT boxes (but
only when all the *clients* of the storage server had public IP addresses,
and were also configured as servers). (#2759, #517)

"Magic Folders" is an experimental two-way directory synchronization tool,
contributed by Least Authority Enterprises, which replaces the previous
experimental (one-way) "drop-upload" feature. This allows multiple users to
keep a single directory in-sync, using Tahoe as the backing store. See
docs/frontends/magic-folder.rst for details and configuration instructions.

Compatibility Issues
--------------------

The old version-1 Introducer protocol has been removed. Tahoe has used the
version-2 protocol since 1.10 (released in 2013), but all nodes (clients,
servers, and the Introducer itself) provided backwards-compatibility
translations when encountering older peers. These translations were finally
removed, so Tahoe nodes at 1.12 or later will not be able to interact with
nodes at 1.9 or older. (#2784)

The versions of Tahoe (1.11.0) and Foolscap (0.6.5) that shipped in
Debian/Jesse (the most recent stable release, as of December 2016) are
regrettably not forwards-compatible with this new version. Nodes running
Jesse will not be able to connect to servers or introducers created with this
release because they cannot parse the new ``tcp:HOST:PORT`` hint syntax (this
syntax has been around for a while, but this is the first Tahoe release to
automatically generate such hints). If you need to work around this, then
after creating your new node, edit the tahoe.cfg of your new
server/introducer: in ``[node] tub.location``, make each connection hint look
like ``HOST:PORT`` instead of ``tcp:HOST:PORT``. If your grid only has nodes
with Foolscap-0.7.0 or later, you will not need this workaround. (#2831)

Nodes now use an Ed25519 public key as a serverid, instead of a Foolscap "tub
id", so status displays will report a different serverid after upgrade. For
the most part this should be self-consistent, however if you have an old
(1.11) client talking to a new (1.12) Helper, then the client's upload
results (on the "Recent Uploads And Downloads" web page) will show unusual
server ids. (#1363)

Dependency/Installation changes
-------------------------------

Tahoe now requires Twisted >= 16.1.0, so ensure that unit tests do not fail
because of uncancelled timers left running by HostnameEndpoint. It also
requires the Tor/I2P supporting code from Foolscap >= 0.12.5 . (#2781)

Configuration Changes
---------------------

Some small changes were made to the way Tahoe-LAFS is configured, via
``tahoe.cfg`` and other files. In general, node behavior should now be more
predictable, and errors should be surfaced earlier.

* ``tub.port`` is now an Endpoint server specification string (which is
  pretty much just like a strports string, but can be extended by plugins).
  It now rejects "tcp:0" and "0". The tahoe.cfg value overrides anything
  stored on disk (in client.port). This should have no effect on most old
  nodes (which did not set tub.port in tahoe.cfg, and which wrote an
  allocated port number to client.port the first time they launched). Folks
  who want to listen on a specific port number typically set tub.port to
  "tcp:12345" or "12345", not "0". (ticket #2491)
* This should enable IPv6 on servers, either via AAAA records or colon-hex
  addresses. (#2827)
* The "portnumfile" (e.g. NODEDIR/client.port) is written as soon as the port
  is allocated, before the tub is created, and only if "tub.port" was empty.
  The old code wrote to it unconditionally, and after Tub startup. So if the
  user allows NODEDIR/client.port to be written, then later modifies
  tahoe.cfg to set "tub.port" to a different value, this difference will
  persist (and the node will honor tahoe.cfg "tub.port" exclusively).
* We now encourage static allocation of tub.port, and pre-configuration of
  the node's externally-reachable IP address or hostname (by setting
  tub.location). Automatic IP-address detection is deprecated. Automatic port
  allocation is discouraged. Both are managed by the new arguments to "tahoe
  create-node".
* "tahoe start" now creates the Tub, and all primary software components,
  before the child process daemonizes. Many configuration errors which would
  previously have been reported in a logfile (after node startup), will now
  be signalled immediately, via stderr. In these cases, the "tahoe start"
  process will exit with a non-zero return code. (#2491)
* Unrecognized tahoe.cfg options are rejected at startup, not ignored (#2809)
* ``tub.port`` can take multple (comma-separated) endpoints, to listen on
  multiple ports at the same time, useful for dual IPv4+IPv6 servers. (#867)
* An empty ``tub.port`` means don't listen at all, which is appropriate for
  client-only nodes (#2816)
* A new setting, ``reveal-ip-address = false``, acts as a safety belt,
  causing an error to be thrown if any other setting might reveal the node's
  IP address (i.e. it requires Tor or I2P to be used, rather than direct TCP
  connections). This is set automatically by ``tahoe create-client
  --hide-ip``. (#1010)

Server-like nodes (Storage Servers and Introducers), created with ``tahoe
create-node`` and ``tahoe create-introducer``, now accept new arguments to
control how they listen for connections, and how they advertise themselves to
other nodes. You can use ``--listen=tcp`` and ``--hostname=`` to choose a
port automatically, or ``--listen=tor`` / ``--listen=i2p`` to use Tor/I2P
hidden services instead. You can also use ``--port=`` and ``--location=`` to
explicitly control the listener and the advertised location. (#2773, #2490)

The "stats-gatherer", used by enterprise deployments to collect runtime
statistics from a fleet of Tahoe storage servers, must now be assigned a
hostname, or location+port pair, at creation time. It will no longer attempt
to guess its location (with /sbin/ifconfig). The "tahoe
create-stats-gatherer" command requires either "--hostname=", or both
"--location=" and "--port". (#2773)

To keep your old stats-gatherers working, with their original FURL, you must
determine a suitable --location and --port, and write their values into
NODEDIR/location and NODEDIR/port, respectively. Or you could simply rebuild
it by re-running "tahoe create-stats-gatherer" with the new arguments.

The stats gatherer now updates a JSON file named "stats.json", instead of a
Pickle named "stats.pickle". The munin plugins in
misc/operations_helpers/munin/ have been updated to match, and must be
re-installed and re-configured if you use munin.

Removed Features
----------------

The "key-generator" node type has been removed. This was a standalone process
that maintained a queue of RSA keys, and clients could offload their
key-generation work by adding "key_generator.furl=" in their tahoe.cfg files,
to create mutable files and directories faster. This seemed important back in
2006, but these days computers are faster and RSA key generation only takes
about 90ms. This removes the "tahoe create-key-generator" command. Any
"key_generator.furl" settings in tahoe.cfg will log a warning and are
otherwise ignored. Attempts to "tahoe start" a previously-generated
key-generator node will result in an error. (#2783)

Tahoe's HTTP Web-API (aka "the WAPI") had an endpoint named "/file/". This
has been deprecated, and applications should use "/named/" instead. (#1903)

The little-used "manhole" debugging feature has been removed. This allowed
you to SSH or Telnet "into" a Tahoe node, providing an interactive
Read-Eval-Print-Loop (REPL) that executed inside the context of the running
process. (#2367)

The "tahoe debug trial" and "tahoe debug repl" CLI commands were removed, as
"tox" is now the preferred way to run tests. (#2735)

One of the "recent uploads and downloads" status pages was using a
Google-hosted API to draw a timing chart of the "mapupdate" operation. This
has been removed, both for privacy (to avoid revealing the serverids to
Google) and because the API was deprecated several years ago. (#1942)

The "_appname.py" feature was removed. Early in Tahoe's history (at
AllMyData), this file allowed the "tahoe" executable to be given a different
name depending upon which Darcs patches were included in the particular
source tree (one for production, another for development, etc). We haven't
needed this for a long time, so it was removed. (#2754)

Other Changes
-------------

Documentation is now hosted at http://tahoe-lafs.readthedocs.io/ (not .org).

Tahoe's testing-only dependencies can now be installed by asking for the
[test] extra, so if you want to set up a virtualenv for testing, use "pip
install -e .[test]" instead just of "pip install -e ." . This includes "tox",
"coverage", "pyflakes", "mock", and all the Tor/I2P extras. Most developer
tooling (code-checks, documentation builds, deprecation warnings, etc) have
been moved from a Makefile into tox environments. (#2776)

The "Welcome" (web) page now shows more detail about the introducer and
storage-server connections, including which connection handler is being used
(tcp/tor/i2p) and why specific connection hints failed to connect. (#2818,
#2819)

The little-used "control port" now uses a separate (ephemeral) Tub. This
means the FURL changes each time the node is restarted, and it only listens
on the loopback (127.0.0.1) interface, on a random port. As the control port
is only used by some automated tests (check_memory, check_speed), this
shouldn't affect anyone. (#2794)

The slightly-more-used "log port" now also uses a separate (ephemeral) Tub,
with the same consequences. The lack of a stable (and externally-reachable)
logport.furl means it is no longer possible to use ``flogtool tail FURL``
against a distant Tahoe server, however ``flogtool tail
.../nodedir/private/logport.furl`` still works just fine (and is the more
common use case anyways). We might bring back the ability to configure the
port and location of the logport in the future, if there is sufficient
demand, but for now it seems better to avoid the complexity.

The default tahoe.cfg setting of ``web.static = public_html``, when
``NODEDIR/public_html/`` does not exist, no longer causes web browsers to
display a traceback which reveals somewhat-private information like the value
of NODEDIR, and the Python/OS versions in use. Instead it just shows a plain
404 error. (#1720)


Release 1.11.0 (30-Mar-2016)
''''''''''''''''''''''''''''

New Build Process
-----------------

``pip install`` (in a virtualenv) is now the recommended way to install
Tahoe-LAFS. The old "bin/tahoe" script (created inside the source tree,
rather than in a virtualenv) has been removed, as has the ancient
"zetuptoolz" fork of setuptools.

Tahoe was started in 2006, and predates pip and virtualenv. From the
very beginning it used a home-made build process that attempted to make
``setup.py build`` behave somewhat like a modern ``pip
install --editable .``. It included a local copy of ``setuptools`` (to
avoid requiring it to be pre-installed), which was then forked as
``zetuptoolz`` to fix bugs during the bad old days of setuptools
non-maintenance. The pseudo-virtualenv used a script named
``bin/tahoe``, created during ``setup.py build``, to set up the $PATH
and $PYTHONPATH as necessary.

Starting with this release, all the custom build process has been
removed, and Tahoe should be installable with standard modern tools. You
will need ``virtualenv`` installed (which provides ``pip`` and
setuptools). Many Python installers include ``virtualenv`` already, and
Debian-like systems can use ``apt-get install python-virtualenv``. If
the command is not available on your system, follow the installation
instructions at https://virtualenv.pypa.io/en/latest/ .

Then, to install the latest version, create a virtualenv and use
``pip``::

    virtualenv venv
    . venv/bin/activate
    (venv) pip install tahoe-lafs
    (venv) tahoe --version

To run Tahoe from a source checkout (so you can hack on Tahoe), use
``pip install --editable .`` from the git tree::

    git clone https://github.com/tahoe-lafs/tahoe-lafs.git
    cd tahoe-lafs
    virtualenv venv
    . venv/bin/activate
    (venv) pip install --editable .
    (venv) tahoe --version

The ``pip install`` will download and install all necessary Python
dependencies. Some dependencies require a C compiler and system
libraries to build: on Debian/Ubuntu-like systems, use ``apt-get install
build-essential python-dev libffi-dev libssl-dev``. On Windows and OS-X
platforms, we provide pre-compiled binary wheels at
``https://tahoe-lafs.org/deps/``, removing the need for a compiler.

(#1582, #2445, also helped to close: #142, #709, #717, #799, #1220,
#1260, #1270, #1403, #1450, #1451, #1504, #1896, #2044, #2221, #2021,
#2028, #2066, #2077, #2247, #2255, #2286, #2306, #2473, #2475, #2530,
#657, #2446, #2439, #2317, #1753, #1009, #1168, #1238, #1258, #1334,
#1346, #1464, #2356, #2570)

New PyPI Distribution Name
--------------------------

Tahoe-LAFS is now known on PyPI as ``tahoe-lafs``. It was formerly known
as ``allmydata-tahoe``. This affects ``pip install`` commands. (#2011)

Because of this change, if you use a git checkout, you may need to run
``make distclean`` (to delete the machine-generated
``src/allmydata/_appname.py`` file). You may also need to remove
``allmydata-tahoe`` from any virtualenvs you've created, before
installing ``tahoe-lafs`` into them. If all else fails, make a new git
checkout, and use a new virtualenv.

Note that the importable *package* name is still ``allmydata``, but this
only affects developers, not end-users. This name scheduled to be
changed in a future release. (#1950)


Compatibility and Dependency Updates
------------------------------------

Tahoe now requires Python 2.7 on all platforms. (#2445)

Tahoe now requires Foolscap 0.10.1, which fixes incompatibilities with
recent Twisted releases. (#2510, #2722, #2567)

Tahoe requires Twisted 15.1.0 or later, so it can request the
``Twisted[tls]`` "extra" (this asks Twisted to ask for everything it
needs to provide proper TLS support). (#2760)

Tests should now work with both Nevow 0.11 and 0.12 . (#2663)

Binary wheels for Windows and OS-X (for all dependencies) have been
built and are hosted at https://tahoe-lafs.org/deps . Use ``pip
install --find-links=URL tahoe-lafs`` to take advantage of them. (#2001)

We've removed the SUMO and tahoe-deps tarballs. Please see
docs/desert-island.rst for instructions to build tahoe from offline
systems. (#1009, #2530, #2446, #2439)

Configuration Changes
---------------------

A new "peers.preferred" item was added to the ``[client]`` section. This
identifies servers that will be promoted to the front of the
peer-selection list when uploading or downloading files. Servers are
identified by their Node ID (visible on the welcome page). This may be
useful to ensure that one full set of shares are placed on nearby
servers, making later downloads fast (and avoid using scarce remote
bandwidth). The remaining shares can go to distant backup servers. (git
commit 96eaca6)

Aliases can now be unicode. (git commit 46719a8b)

The introducer's "set_encoding_parameters" feature was removed. Once
upon a time, the Introducer could recommend encoding parameters
(shares.needed and shares.total) to all clients, the idea being that the
Introducer had a slightly better idea about the expected size of the
storage server pool than clients might. Client-side support for this was
removed long ago, but the Introducer itself kept delivering
recommendations until this release. (git commit 56a9f5ad)

Other Fixes
-----------

The OS-X .pkg installer has been improved slightly, to clean up after
previous installations better. (#2493)

All WUI (Web UI) timestamps should now be a consistent format, using the
gateway machine's local time zone. (#1077)

The web "welcome page" has been improved: it shows how long a server has
been connected (in minutes/hours/days, instead of the date+time when the
connection was most recently established). The "announced" column has
been replaced with "Last RX" column that shows when we last heard
anything from the server. The mostly-useless "storage" column has been
removed. (#1973)

In the ``tahoe ls`` command, the ``-u`` shortcut for ``--uri`` has been
removed, leaving the shortcut free for the global ``--node-url`` option.
(#1949, #2137)

Some internal logging was disabled, to avoid a temporary bug that caused
excessive (perhaps infinite) log messages to be created. (#2567)

Other non-user-visible tickets were fixed. (#2499, #2511, #2556, #2663,
#2723, #2543)


Release 1.10.2 (2015-07-30)
'''''''''''''''''''''''''''

Packaging Changes
-----------------

This release no longer requires the ``mock`` library (which was previously
used in the unit test suite). Shortly after the Tahoe-LAFS 1.10.1 release, a
new version of ``mock`` was released (1.1.0) that proved to be incompatible
with Tahoe's fork of setuptools, preventing Tahoe-1.10.1 from building at
all. `#2465`_

The ``tahoe --version`` output is now less likely to include scary diagnostic
warnings that look deceptively like stack traces. `#2436`_

The pyasn1 requirement was increased to >= 0.1.8.

.. _`#2465`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2465
.. _`#2436`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2436

Other Fixes
-----------

A WebAPI ``GET`` would sometimes hang when using the HTTP Range header to
read just part of the file. `#2459`_

Using ``tahoe cp`` to copy two different files of the same name into the same
target directory now raises an error, rather than silently overwriting one of
them. `#2447`_

All tickets closed in this release: 2328 2436 2446 2447 2459 2460 2461 2462
2465 2470.

.. _`#2459`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2459
.. _`#2447`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2447


Release 1.10.1 (2015-06-15)
'''''''''''''''''''''''''''

User Interface / Configuration Changes
--------------------------------------

The "``tahoe cp``" CLI command's ``--recursive`` option is now more predictable,
but behaves slightly differently than before. See below for details. Tickets
`#712`_, `#2329`_.

The SFTP server can now use public-key authentication (instead of only
password-based auth). Public keys are configured through an "account file",
just like passwords. See docs/frontends/FTP-and-SFTP for examples of the
format. `#1411`_

The Tahoe node can now be configured to disable automatic IP-address
detection. Using "AUTO" in tahoe.cfg [node]tub.location= (which is now the
default) triggers autodetection. Omit "AUTO" to disable autodetection. "AUTO"
can be combined with static addresses to e.g. use both a stable
UPnP-configured tunneled address and a DHCP-assigned dynamic (local subnet
only) address. See `configuration.rst`_ for details. `#754`_

The web-based user interface ("WUI") Directory and Welcome pages have been
redesigned, with improved CSS for narrow windows and more-accessible icons
(using distinctive shapes instead of just colors). `#1931`_ `#1961`_ `#1966`_
`#1972`_ `#1901`_

.. _`#712`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/712
.. _`#754`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/754
.. _`#1411`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1411
.. _`#1901`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1901
.. _`#1931`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1931
.. _`#1961`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1961
.. _`#1966`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1966
.. _`#1972`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1972
.. _`#2329`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2329
.. _`configuration.rst`: docs/configuration.rst

"tahoe cp" changes
------------------

The many ``cp``-like tools in the Unix world (POSIX ``/bin/cp``, the ``scp``
provided by SSH, ``rsync``) all behave slightly differently in unusual
circumstances, especially when copying whole directories into a target that
may or may not already exist. The most common difference is whether the user
is referring to the source directory as a whole, or to its contents. For
example, should "``cp -r foodir bardir``" create a new directory named
"``bardir/foodir``"? Or should it behave more like "``cp -r foodir/* bardir``"?
Some tools use the presence of a trailing slash to indicate which behavior
you want. Others ignore trailing slashes.

"``tahoe cp``" is no exception to having exceptional cases. This release fixes
some bad behavior and attempts to establish a consistent rationale for its
behavior. The new rule is:

- If the thing being copied is a directory, and it has a name (e.g. it's not
  a raw Tahoe-LAFS directorycap), then you are referring to the directory
  itself.
- If the thing being copied is an unnamed directory (e.g. raw dircap or
  alias), then you are referring to the contents.
- Trailing slashes do not affect the behavior of the copy (although putting
  a trailing slash on a file-like target is an error).
- The "``-r``" (``--recursive``) flag does not affect the behavior of the
  copy (although omitting ``-r`` when the source is a directory is an error).
- If the target refers to something that does not yet exist:
  - and if the source is a single file, then create a new file;
  - otherwise, create a directory.

There are two main cases where the behavior of Tahoe-LAFS v1.10.1 differs
from that of the previous v1.10.0 release:

- "``cp DIRCAP/file.txt ./local/missing``" , where "``./local``" is a
  directory but "``./local/missing``" does not exist. The implication is
  that you want Tahoe to create a new file named "``./local/missing``" and
  fill it with the contents of the Tahoe-side ``DIRCAP/file.txt``. In
  v1.10.0, a plain "``cp``" would do just this, but "``cp -r``" would do
  "``mkdir ./local/missing``" and then create a file named
  "``./local/missing/file.txt``". In v1.10.1, both "``cp``" and "``cp -r``"
  create a file named "``./local/missing``".
- "``cp -r PARENTCAP/dir ./local/missing``", where ``PARENTCAP/dir/``
  contains "``file.txt``", and again "``./local``" is a directory but
  "``./local/missing``" does not exist. In both v1.10.0 and v1.10.1, this
  first does "``mkdir ./local/missing``". In v1.10.0, it would then copy
  the contents of the source directory into the new directory, resulting
  in "``./local/missing/file.txt``". In v1.10.1, following the new rule
  of "a named directory source refers to the directory itself", the tool
  creates "``./local/missing/dir/file.txt``".

Compatibility and Dependency Updates
------------------------------------

Windows now requires Python 2.7. Unix/OS-X platforms can still use either
Python 2.6 or 2.7, however this is probably the last release that will
support 2.6 (it is no longer receiving security updates, and most OS
distributions have switched to 2.7). Tahoe-LAFS now has the following
dependencies:

- Twisted >= 13.0.0
- Nevow >= 0.11.1
- foolscap >= 0.8.0
- service-identity
- characteristic >= 14.0.0
- pyasn1 >= 0.1.4
- pyasn1-modules >= 0.0.5

On Windows, if pywin32 is not installed then the dependencies on Twisted
and Nevow become:

- Twisted >= 11.1.0, <= 12.1.0
- Nevow >= 0.9.33, <= 0.10

On all platforms, if pyOpenSSL >= 0.14 is installed, then it will be used,
but if not then only pyOpenSSL >= 0.13, <= 0.13.1 will be built when directly
invoking `setup.py build` or `setup.py install`.

We strongly advise OS packagers to take the option of making a tahoe-lafs
package depend on pyOpenSSL >= 0.14. In order for that to work, the following
additional Python dependencies are needed:

- cryptography
- cffi >= 0.8
- six >= 1.4.1
- enum34
- pycparser

as well as libffi (for Debian/Ubuntu, the name of the needed OS package is
`libffi6`).

Tahoe-LAFS is now compatible with Setuptools version 8 and Pip version 6 or
later, which should fix execution on Ubuntu 15.04 (it now tolerates PEP440
semantics in dependency specifications). `#2354`_ `#2242`_

Tahoe-LAFS now depends upon foolscap-0.8.0, which creates better private keys
and certificates than previous versions. To benefit from the improvements
(2048-bit RSA keys and SHA256-based certificates), you must re-generate your
Tahoe nodes (which changes their TubIDs and FURLs). `#2400`_

.. _`#2242`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2242
.. _`#2354`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2354
.. _`#2400`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2400

Packaging
---------

A preliminary OS-X package, named "``tahoe-lafs-VERSION-osx.pkg``", is now
being generated. It is a standard double-clickable installer, which creates
``/Applications/tahoe.app`` that embeds a complete runtime tree. However
launching the ``.app`` only brings up a notice on how to run tahoe from the
command line. A future release may turn this into a fully-fledged application
launcher. `#182`_ `#2393`_ `#2323`_

Preliminary Docker support was added. Tahoe container images may be available
on DockerHub. `PR#165`_ `#2419`_ `#2421`_

Old and obsolete Debian packaging tools have been removed. `#2282`_

.. _`#182`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/182
.. _`#2282`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2282
.. _`#2323`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2323
.. _`#2393`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2393
.. _`#2419`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2419
.. _`#2421`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2421
.. _`PR#165`: https://github.com/tahoe-lafs/tahoe-lafs/pull/165

Minor Changes
-------------

- Welcome page: add per-server "(space) Available" column. `#648`_
- check/deep-check learned to accept multiple location arguments. `#740`_
- Checker reports: remove needs-rebalancing, add count-happiness. `#1784`_ `#2105`_
- CLI ``--help``: cite (but don't list) global options on each command. `#2233`_
- Fix ftp "``ls``" to work with Twisted 15.0.0. `#2394`_

.. _`#648`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/648
.. _`#740`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/740
.. _`#1784`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1784
.. _`#2105`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2105
.. _`#2233`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2233
.. _`#2394`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2394

Roughly 75 tickets were closed in this release: 623 648 712 740 754 898 1146
1159 1336 1381 1411 1634 1674 1698 1707 1717 1737 1784 1800 1807 1842 1847
1901 1918 1953 1960 1961 1966 1969 1972 1974 1988 1992 2005 2008 2023 2027
2028 2034 2048 2067 2086 2105 2121 2128 2165 2193 2208 2209 2233 2235 2242
2245 2248 2249 2249 2280 2281 2282 2290 2305 2312 2323 2340 2354 2380 2393
2394 2398 2400 2415 2416 2417 2433. Another dozen were referenced but not
closed: 182 666 982 1064 1258 1531 1536 1742 1834 1931 1935 2286. Roughly 40
GitHub pull-requests were closed: 32 48 50 56 57 61 62 62 63 64 69 73 81 82
84 85 87 91 94 95 96 103 107 109 112 114 120 122 125 126 133 135 136 137 142
146 149 152 165.

For more information about any ticket, visit e.g.
https://tahoe-lafs.org/trac/tahoe-lafs/ticket/754


Release 1.10.0 (2013-05-01)
'''''''''''''''''''''''''''

New Features
------------

- The Welcome page has been redesigned. This is a preview of the design style
  that is likely to be used in other parts of the WUI in future Tahoe-LAFS
  versions. (`#1713`_, `#1457`_, `#1735`_)
- A new extensible Introducer protocol has been added, as the basis for
  future improvements such as accounting. Compatibility with older nodes is
  not affected. When server, introducer, and client are all upgraded, the
  welcome page will show node IDs that start with "v0-" instead of the old
  tubid. See `<docs/nodekeys.rst>`__ for details. (`#466`_)
- The web-API has a new ``relink`` operation that supports directly moving
  files between directories. (`#1579`_)

Security Improvements
---------------------

- The ``introducer.furl`` for new Introducers is now unguessable. In previous
  releases, this FURL used a predictable swissnum, allowing a network
  eavesdropper who observes any node connecting to the Introducer to access
  the Introducer themselves, and thus use servers or offer storage service to
  clients (i.e. "join the grid"). In the new code, the only way to join a
  grid is to be told the ``introducer.furl`` by someone who already knew it.
  Note that pre-existing introducers are not changed. To force an introducer
  to generate a new FURL, delete the existing ``introducer.furl`` file and
  restart it. After doing this, the ``[client]introducer.furl`` setting of
  every client and server that should connect to that introducer must be
  updated. Note that other users of a shared machine may be able to read
  ``introducer.furl`` from your ``tahoe.cfg`` file unless you configure the
  file permissions to prevent them. (`#1802`_)
- Both ``introducer.furl`` and ``helper.furl`` are now censored from the
  Welcome page, to prevent users of your gateway from learning enough to
  create gateway nodes of their own.  For existing guessable introducer
  FURLs, the ``introducer`` swissnum is still displayed to show that a
  guessable FURL is in use. (`#860`_)

Command-line Syntax Changes
---------------------------

- Global options to ``tahoe``, such as ``-d``/``--node-directory``, must now
  come before rather than after the command name (for example,
  ``tahoe -d BASEDIR cp -r foo: bar:`` ). (`#166`_)

Notable Bugfixes
----------------

- In earlier versions, if a connection problem caused a download failure for
  an immutable file, subsequent attempts to download the same file could also
  fail. This is now fixed. (`#1679`_)
- Filenames in WUI directory pages are now displayed correctly when they
  contain characters that require HTML escaping. (`#1143`_)
- Non-ASCII node nicknames no longer cause WUI errors. (`#1298`_)
- Checking a LIT file using ``tahoe check`` no longer results in an
  exception. (`#1758`_)
- The SFTP frontend now works with recent versions of Twisted, rather than
  giving errors or warnings about use of ``IFinishableConsumer``. (`#1926`_,
  `#1564`_, `#1525`_)
- ``tahoe cp --verbose`` now counts the files being processed correctly.
  (`#1805`_, `#1783`_)
- Exceptions no longer trigger an unhelpful crash reporter on Ubuntu 12.04
  ("Precise") or later. (`#1746`_)
- The error message displayed when a CLI tool cannot connect to a gateway has
  been improved. (`#974`_)
- Other minor fixes: `#1781`_, `#1812`_, `#1915`_, `#1484`_, `#1525`_

Compatibility and Dependencies
------------------------------

- Python >= 2.6, except Python 3 (`#1658`_)
- Twisted >= 11.0.0 (`#1771`_)
- mock >= 0.8 (for unit tests)
- pycryptopp >= 0.6.0 (for Ed25519 signatures)
- zope.interface >= 3.6.0 (except 3.6.3 or 3.6.4)

Other Changes
-------------

- The ``flogtool`` utility, used to read detailed event logs, can now be
  accessed as ``tahoe debug flogtool`` even when Foolscap is not installed
  system-wide. (`#1693`_)
- The provisioning/reliability pages were removed from the main client's web
  interface, and moved into a standalone web-based tool. Use the ``run.py``
  script in ``misc/operations_helpers/provisioning/`` to access them.
- Web clients can now cache (ETag) immutable directory pages. (`#443`_)
- `<docs/convergence_secret.rst>`__ was added to document the adminstration
  of convergence secrets. (`#1761`_)

Precautions when Upgrading
--------------------------

- When upgrading a grid from a recent revision of trunk, follow the
  precautions from this `message to the tahoe-dev mailing list`_, to ensure
  that announcements to the Introducer are recognized after the upgrade.
  This is not necessary when upgrading from a previous release like 1.9.2.

.. _`#166`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/166
.. _`#443`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/443
.. _`#466`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/466
.. _`#860`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/860
.. _`#974`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/974
.. _`#1143`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1143
.. _`#1298`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1298
.. _`#1457`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1457
.. _`#1484`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1484
.. _`#1525`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1525
.. _`#1564`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1564
.. _`#1579`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1579
.. _`#1658`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1658
.. _`#1679`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1679
.. _`#1693`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1693
.. _`#1713`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1713
.. _`#1735`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1735
.. _`#1746`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1746
.. _`#1758`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1758
.. _`#1761`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1761
.. _`#1771`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1771
.. _`#1781`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1781
.. _`#1783`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1783
.. _`#1802`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1802
.. _`#1805`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1805
.. _`#1812`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1812
.. _`#1915`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1915
.. _`#1926`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1926
.. _`message to the tahoe-dev mailing list`:
             https://lists.tahoe-lafs.org/pipermail/tahoe-dev/2013-March/008079.html


Release 1.9.2 (2012-07-03)
''''''''''''''''''''''''''

Notable Bugfixes
----------------

- Several regressions in support for reading (`#1636`_), writing/modifying
  (`#1670`_, `#1749`_), verifying (`#1628`_) and repairing (`#1655`_, `#1669`_,
  `#1676`_, `#1689`_) mutable files have been fixed.
- FTP can now list directories containing mutable files, although it
  still does not support reading or writing mutable files. (`#680`_)
- The FTP frontend would previously show Jan 1 1970 for all timestamps;
  now it shows the correct modification time of the directory entry.
  (`#1688`_)
- If a node is configured to report incidents to a log gatherer, but the
  gatherer is offline when some incidents occur, it would previously not
  "catch up" with those incidents as intended. (`#1725`_)
- OpenBSD 5 is now supported. (`#1584`_)
- The ``count-good-share-hosts`` field of file check results is now
  computed correctly. (`#1115`_)

Configuration/Behavior Changes
------------------------------

- The capability of the upload directory for the drop-upload frontend
  is now specified in the file ``private/drop_upload_dircap`` under
  the gateway's node directory, rather than in its ``tahoe.cfg``.
  (`#1593`_)

Packaging Changes
-----------------

- Tahoe-LAFS can be built correctly from a git repository as well as
  from darcs.

Compatibility and Dependencies
------------------------------

- foolscap >= 0.6.3 is required, in order to make Tahoe-LAFS compatible
  with Twisted >= 11.1.0. (`#1788`_)
- Versions 2.0.1 and 2.4 of PyCrypto are excluded. (`#1631`_, `#1574`_)

.. _`#680`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/680
.. _`#1115`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1115
.. _`#1574`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1574
.. _`#1584`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1584
.. _`#1593`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1593
.. _`#1628`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1628
.. _`#1631`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1631
.. _`#1636`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1636
.. _`#1655`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1655
.. _`#1669`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1669
.. _`#1670`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1670
.. _`#1676`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1676
.. _`#1688`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1688
.. _`#1689`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1689
.. _`#1725`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1725
.. _`#1749`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1749
.. _`#1788`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1788


Release 1.9.1 (2012-01-12)
''''''''''''''''''''''''''

Security-related Bugfix
-----------------------

- Fix flaw that would allow servers to cause undetected corruption when
  retrieving the contents of mutable files (both SDMF and MDMF). (`#1654`_)

.. _`#1654`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1654


Release 1.9.0 (2011-10-30)
''''''''''''''''''''''''''

New Features
------------

- The most significant new feature in this release is MDMF: "Medium-size
  Distributed Mutable Files". Unlike standard SDMF files, these provide
  efficient partial-access (reading and modifying small portions of the file
  instead of the whole thing). MDMF is opt-in (it is not yet the default
  format for mutable files), both to ensure compatibility with previous
  versions, and because the algorithm does not yet meet memory-usage goals.
  Enable it with ``--format=MDMF`` in the CLI (``tahoe put`` and ``tahoe
  mkdir``), or the "format" radioboxes in the web interface. See
  `<docs/specifications/mutable.rst>`__ for more details (`#393`_, `#1507`_)
- A "blacklist" feature allows blocking access to specific files through
  a particular gateway. See the "Access Blacklist" section of
  `<docs/configuration.rst>`__ for more details. (`#1425`_)
- A "drop-upload" feature has been added, which allows you to upload
  files to a Tahoe-LAFS directory just by writing them to a local
  directory. This feature is experimental and should not be relied on
  to store the only copy of valuable data. It is currently available
  only on Linux. See `<docs/frontends/drop-upload.rst>`__ for documentation.
  (`#1429`_)
- The timeline of immutable downloads can be viewed using a zoomable and
  pannable JavaScript-based visualization. This is accessed using the
  'timeline' link on the File Download Status page for the download, which
  can be reached from the Recent Uploads and Downloads page.

Configuration/Behavior Changes
------------------------------

- Prior to Tahoe-LAFS v1.3, the configuration of some node options could
  be specified using individual config files rather than via ``tahoe.cfg``.
  These files now cause an error if present. (`#1385`_)
- Storage servers now calculate their remaining space based on the filesystem
  containing the ``storage/shares/`` directory. Previously they looked at the
  filesystem containing the ``storage/`` directory. This allows
  ``storage/shares/``, rather than ``storage/``, to be a mount point or a
  symlink pointing to another filesystem. (`#1384`_)
- ``tahoe cp xyz MUTABLE`` will modify the existing mutable file instead of
  creating a new one. (`#1304`_)
- The button for unlinking a file from its directory on a WUI directory
  listing is now labelled "unlink" rather than "del". (`#1104`_)

Notable Bugfixes
----------------

- The security bugfix for the vulnerability allowing deletion of shares,
  detailed in the news for v1.8.3 below, is also included in this
  release. (`#1528`_)
- Some cases of immutable upload, for example using the ``tahoe put`` and
  ``tahoe cp`` commands or SFTP, did not appear in the history of Recent
  Uploads and Downloads. (`#1079`_)
- The memory footprint of the verifier has been reduced by serializing
  block fetches. (`#1395`_)
- Large immutable downloads are now a little faster than in v1.8.3 (about
  5% on a fast network). (`#1268`_)

Packaging Changes
-----------------

- The files related to Debian packaging have been removed from the Tahoe
  source tree, since they are now maintained as part of the official
  Debian packages. (`#1454`_)
- The unmaintained FUSE plugins were removed from the source tree. See
  ``docs/frontends/FTP-and-SFTP.rst`` for how to mount a Tahoe filesystem on
  Unix via sshfs. (`#1409`_)
- The Tahoe licenses now give explicit permission to combine Tahoe-LAFS
  with code distributed under the following additional open-source licenses
  (any version of each):

  * Academic Free License
  * Apple Public Source License
  * BitTorrent Open Source License
  * Lucent Public License
  * Jabber Open Source License
  * Common Development and Distribution License
  * Microsoft Public License
  * Microsoft Reciprocal License
  * Sun Industry Standards Source License
  * Open Software License

Compatibility and Dependencies
------------------------------

- To resolve an incompatibility between Nevow and zope.interface (versions
  3.6.3 and 3.6.4), Tahoe-LAFS now requires an earlier or later
  version of zope.interface. (`#1435`_)
- The Twisted dependency has been raised to version 10.1 to ensure we no
  longer require pywin32 on Windows, the new drop-upload feature has the
  required support from Twisted on Linux, and that it is never necessary to
  patch Twisted in order to use the FTP frontend. (`#1274`_, `#1429`_,
  `#1438`_)
- An explicit dependency on pyOpenSSL has been added, replacing the indirect
  dependency via the "secure_connections" option of foolscap. (`#1383`_)

Minor Changes
-------------

- A ``man`` page has been added (`#1420`_). All other docs are in ReST
  format.
- The ``tahoe_files`` munin plugin reported an incorrect count of the number
  of share files. (`#1391`_)
- Minor documentation updates: #627, #1104, #1225, #1297, #1342, #1404
- Other minor changes: #636, #1355, #1363, #1366, #1388, #1392, #1412, #1344,
  #1347, #1359, #1389, #1441, #1442, #1446, #1474, #1503

.. _`#393`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/393
.. _`#1079`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1079
.. _`#1104`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1104
.. _`#1268`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1268
.. _`#1274`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1274
.. _`#1304`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1304
.. _`#1383`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1383
.. _`#1384`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1384
.. _`#1385`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1385
.. _`#1391`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1391
.. _`#1395`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1395
.. _`#1409`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1409
.. _`#1420`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1420
.. _`#1425`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1425
.. _`#1429`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1429
.. _`#1435`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1435
.. _`#1438`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1438
.. _`#1454`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1454
.. _`#1507`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1507


Release 1.8.3 (2011-09-13)
''''''''''''''''''''''''''

Security-related Bugfix
-----------------------

- Fix flaw that would allow a person who knows a storage index of a file to
  delete shares of that file. (`#1528`_)
- Remove corner cases in mutable file bounds management which could expose
  extra lease info or old share data (from prior versions of the mutable
  file) if someone with write authority to that mutable file exercised these
  corner cases in a way that no actual Tahoe-LAFS client does. (Probably not
  exploitable.) (`#1528`_)

.. _`#1528`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1528


Release 1.8.2 (2011-01-30)
''''''''''''''''''''''''''

Compatibility and Dependencies
------------------------------

- Tahoe is now compatible with Twisted-10.2 (released last month), as
  well as with earlier versions. The previous Tahoe-1.8.1 release
  failed to run against Twisted-10.2, raising an AttributeError on
  StreamServerEndpointService (`#1286`_)
- Tahoe now depends upon the "mock" testing library, and the foolscap
  dependency was raised to 0.6.1 . It no longer requires pywin32
  (which was used only on windows). Future developers should note that
  reactor.spawnProcess and derivatives may no longer be used inside
  Tahoe code.

Other Changes
-------------

- the default reserved_space value for new storage nodes is 1 GB
  (`#1208`_)
- documentation is now in reStructuredText (.rst) format
- "tahoe cp" should now handle non-ASCII filenames
- the unmaintained Mac/Windows GUI applications have been removed
  (`#1282`_)
- tahoe processes should appear in top and ps as "tahoe", not
  "python", on some unix platforms. (`#174`_)
- "tahoe debug trial" can be used to run the test suite (`#1296`_)
- the SFTP frontend now reports unknown sizes as "0" instead of "?",
  to improve compatibility with clients like FileZilla (`#1337`_)
- "tahoe --version" should now report correct values in situations
  where 1.8.1 might have been wrong (`#1287`_)

.. _`#1208`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1208
.. _`#1282`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1282
.. _`#1286`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1286
.. _`#1287`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1287
.. _`#1296`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1296
.. _`#1337`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1337


Release 1.8.1 (2010-10-28)
''''''''''''''''''''''''''

Bugfixes and Improvements
-------------------------

- Allow the repairer to improve the health of a file by uploading some
  shares, even if it cannot achieve the configured happiness
  threshold. This fixes a regression introduced between v1.7.1 and
  v1.8.0. (`#1212`_)
- Fix a memory leak in the ResponseCache which is used during mutable
  file/directory operations. (`#1045`_)
- Fix a regression and add a performance improvement in the
  downloader.  This issue caused repair to fail in some special
  cases. (`#1223`_)
- Fix a bug that caused 'tahoe cp' to fail for a grid-to-grid copy
  involving a non-ASCII filename. (`#1224`_)
- Fix a rarely-encountered bug involving printing large strings to the
  console on Windows. (`#1232`_)
- Perform ~ expansion in the --exclude-from filename argument to
  'tahoe backup'. (`#1241`_)
- The CLI's 'tahoe mv' and 'tahoe ln' commands previously would try to
  use an HTTP proxy if the HTTP_PROXY environment variable was set.
  These now always connect directly to the WAPI, thus avoiding giving
  caps to the HTTP proxy (and also avoiding failures in the case that
  the proxy is failing or requires authentication). (`#1253`_)
- The CLI now correctly reports failure in the case that 'tahoe mv'
  fails to unlink the file from its old location. (`#1255`_)
- 'tahoe start' now gives a more positive indication that the node has
  started. (`#71`_)
- The arguments seen by 'ps' or other tools for node processes are now
  more useful (in particular, they include the path of the 'tahoe'
  script, rather than an obscure tool named 'twistd'). (`#174`_)

Removed Features
----------------

- The tahoe start/stop/restart and node creation commands no longer
  accept the -m or --multiple option, for consistency between
  platforms.  (`#1262`_)

Packaging
---------

- We now host binary packages so that users on certain operating
  systems can install without having a compiler.
  <https://tahoe-lafs.org/source/tahoe-lafs/deps/tahoe-lafs-dep-eggs/README.html>
- Use a newer version of a dependency if needed, even if an older
  version is installed. This would previously cause a VersionConflict
  error. (`#1190`_)
- Use a precompiled binary of a dependency if one with a sufficiently
  high version number is available, instead of attempting to compile
  the dependency from source, even if the source version has a higher
  version number. (`#1233`_)

Documentation
-------------

- All current documentation in .txt format has been converted to .rst
  format. (`#1225`_)
- Added docs/backdoors.rst declaring that we won't add backdoors to
  Tahoe-LAFS, or add anything to facilitate government access to data.
  (`#1216`_)

.. _`#71`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/71
.. _`#174`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/174
.. _`#1212`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1212
.. _`#1045`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1045
.. _`#1190`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1190
.. _`#1216`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1216
.. _`#1223`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1223
.. _`#1224`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1224
.. _`#1225`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1225
.. _`#1232`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1232
.. _`#1233`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1233
.. _`#1241`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1241
.. _`#1253`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1253
.. _`#1255`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1255
.. _`#1262`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1262


Release 1.8.0 (2010-09-23)
''''''''''''''''''''''''''

New Features
------------

- A completely new downloader which improves performance and
  robustness of immutable-file downloads. It uses the fastest K
  servers to download the data in K-way parallel. It automatically
  fails over to alternate servers if servers fail in mid-download. It
  allows seeking to arbitrary locations in the file (the previous
  downloader which would only read the entire file sequentially from
  beginning to end). It minimizes unnecessary round trips and
  unnecessary bytes transferred to improve performance. It sends
  requests to fewer servers to reduce the load on servers (the
  previous one would send a small request to every server for every
  download) (`#287`_, `#288`_, `#448`_, `#798`_, `#800`_, `#990`_,
  `#1170`_, `#1191`_)
- Non-ASCII command-line arguments and non-ASCII outputs now work on
  Windows. In addition, the command-line tool now works on 64-bit
  Windows. (`#1074`_)

Bugfixes and Improvements
-------------------------

- Document and clean up the command-line options for specifying the
  node's base directory. (`#188`_, `#706`_, `#715`_, `#772`_,
  `#1108`_)
- The default node directory for Windows is ".tahoe" in the user's
  home directory, the same as on other platforms. (`#890`_)
- Fix a case in which full cap URIs could be logged. (`#685`_,
  `#1155`_)
- Fix bug in WUI in Python 2.5 when the system clock is set back to
  1969. Now you can use Tahoe-LAFS with Python 2.5 and set your system
  clock to 1969 and still use the WUI. (`#1055`_)
- Many improvements in code organization, tests, logging,
  documentation, and packaging. (`#983`_, `#1074`_, `#1108`_,
  `#1127`_, `#1129`_, `#1131`_, `#1166`_, `#1175`_)

Dependency Updates
------------------

- on x86 and x86-64 platforms, pycryptopp >= 0.5.20
- pycrypto 2.2 is excluded due to a bug

.. _`#188`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/188
.. _`#288`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/288
.. _`#448`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/448
.. _`#685`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/685
.. _`#706`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/706
.. _`#715`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/715
.. _`#772`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/772
.. _`#798`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/798
.. _`#800`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/800
.. _`#890`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/890
.. _`#983`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/983
.. _`#990`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/990
.. _`#1055`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1055
.. _`#1074`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1074
.. _`#1108`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1108
.. _`#1155`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1155
.. _`#1170`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1170
.. _`#1191`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1191
.. _`#1127`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1127
.. _`#1129`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1129
.. _`#1131`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1131
.. _`#1166`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1166
.. _`#1175`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1175

Release 1.7.1 (2010-07-18)
''''''''''''''''''''''''''

Bugfixes and Improvements
-------------------------

- Fix bug in which uploader could fail with AssertionFailure or report
  that it had achieved servers-of-happiness when it hadn't. (`#1118`_)
- Fix bug in which servers could get into a state where they would
  refuse to accept shares of a certain file (`#1117`_)
- Add init scripts for managing the gateway server on Debian/Ubuntu
  (`#961`_)
- Fix bug where server version number was always 0 on the welcome page
  (`#1067`_)
- Add new command-line command "tahoe unlink" as a synonym for "tahoe
  rm" (`#776`_)
- The FTP frontend now encrypts its temporary files, protecting their
  contents from an attacker who is able to read the disk. (`#1083`_)
- Fix IP address detection on FreeBSD 7, 8, and 9 (`#1098`_)
- Fix minor layout issue in the Web User Interface with Internet
  Explorer (`#1097`_)
- Fix rarely-encountered incompatibility between Twisted logging
  utility and the new unicode support added in v1.7.0 (`#1099`_)
- Forward-compatibility improvements for non-ASCII caps (`#1051`_)

Code improvements
-----------------

- Simplify and tidy-up directories, unicode support, test code
  (`#923`_, `#967`_, `#1072`_)

.. _`#776`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/776
.. _`#923`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/923
.. _`#961`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/961
.. _`#967`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/967
.. _`#1051`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1051
.. _`#1067`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1067
.. _`#1072`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1072
.. _`#1083`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1083
.. _`#1097`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1097
.. _`#1098`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1098
.. _`#1099`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1099
.. _`#1117`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1117
.. _`#1118`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1118


Release 1.7.0 (2010-06-18)
''''''''''''''''''''''''''

New Features
------------

- SFTP support (`#1037`_)
  Your Tahoe-LAFS gateway now acts like a full-fledged SFTP server. It
  has been tested with sshfs to provide a virtual filesystem in Linux.
  Many users have asked for this feature.  We hope that it serves them
  well! See the `FTP-and-SFTP.rst`_ document to get
  started.
- support for non-ASCII character encodings (`#534`_)
  Tahoe-LAFS now correctly handles filenames containing non-ASCII
  characters on all supported platforms:

 - when reading files in from the local filesystem (such as when you
   run "tahoe backup" to back up your local files to a Tahoe-LAFS
   grid);
 - when writing files out to the local filesystem (such as when you
   run "tahoe cp -r" to recursively copy files out of a Tahoe-LAFS
   grid);
 - when displaying filenames to the terminal (such as when you run
   "tahoe ls"), subject to limitations of the terminal and locale;
 - when parsing command-line arguments, except on Windows.

- Servers of Happiness (`#778`_)
  Tahoe-LAFS now measures during immutable file upload to see how well
  distributed it is across multiple servers. It aborts the upload if
  the pieces of the file are not sufficiently well-distributed.
  This behavior is controlled by a configuration parameter called
  "servers of happiness". With the default settings for its erasure
  coding, Tahoe-LAFS generates 10 shares for each file, such that any
  3 of those shares are sufficient to recover the file. The default
  value of "servers of happiness" is 7, which means that Tahoe-LAFS
  will guarantee that there are at least 7 servers holding some of the
  shares, such that any 3 of those servers can completely recover your
  file.  The new upload code also distributes the shares better than the
  previous version in some cases and takes better advantage of
  pre-existing shares (when a file has already been previously
  uploaded). See the `architecture.rst`_ document [3] for details.

Bugfixes and Improvements
-------------------------

- Premature abort of upload if some shares were already present and
  some servers fail. (`#608`_)
- python ./setup.py install -- can't create or remove files in install
  directory. (`#803`_)
- Network failure => internal TypeError. (`#902`_)
- Install of Tahoe on CentOS 5.4. (`#933`_)
- CLI option --node-url now supports https url. (`#1028`_)
- HTML/CSS template files were not correctly installed under
  Windows. (`#1033`_)
- MetadataSetter does not enforce restriction on setting "tahoe"
  subkeys.  (`#1034`_)
- ImportError: No module named
  setuptools_darcs.setuptools_darcs. (`#1054`_)
- Renamed Title in xhtml files. (`#1062`_)
- Increase Python version dependency to 2.4.4, to avoid a critical
  CPython security bug. (`#1066`_)
- Typo correction for the munin plugin tahoe_storagespace. (`#968`_)
- Fix warnings found by pylint. (`#973`_)
- Changing format of some documentation files. (`#1027`_)
- the misc/ directory was tied up. (`#1068`_)
- The 'ctime' and 'mtime' metadata fields are no longer written except
  by "tahoe backup". (`#924`_)
- Unicode filenames in Tahoe-LAFS directories are normalized so that
  names that differ only in how accents are encoded are treated as the
  same. (`#1076`_)
- Various small improvements to documentation. (`#937`_, `#911`_,
  `#1024`_, `#1082`_)

Removals
--------

- The 'tahoe debug consolidate' subcommand (for converting old
  allmydata Windows client backups to a newer format) has been
  removed.

Dependency Updates
------------------

- the Python version dependency is raised to 2.4.4 in some cases
  (2.4.3 for Redhat-based Linux distributions, 2.4.2 for UCS-2 builds)
  (`#1066`_)
- pycrypto >= 2.0.1
- pyasn1 >= 0.0.8a
- mock (only required by unit tests)

.. _`#534`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/534
.. _`#608`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/608
.. _`#778`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/778
.. _`#803`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/803
.. _`#902`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/902
.. _`#911`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/911
.. _`#924`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/924
.. _`#937`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/937
.. _`#933`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/933
.. _`#968`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/968
.. _`#973`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/973
.. _`#1024`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1024
.. _`#1027`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1027
.. _`#1028`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1028
.. _`#1033`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1033
.. _`#1034`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1034
.. _`#1037`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1037
.. _`#1054`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1054
.. _`#1062`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1062
.. _`#1066`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1066
.. _`#1068`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1068
.. _`#1076`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1076
.. _`#1082`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1082
.. _architecture.rst: docs/architecture.rst
.. _FTP-and-SFTP.rst: docs/frontends/FTP-and-SFTP.rst

Release 1.6.1 (2010-02-27)
''''''''''''''''''''''''''

Bugfixes
--------

- Correct handling of Small Immutable Directories

  Immutable directories can now be deep-checked and listed in the web
  UI in all cases. (In v1.6.0, some operations, such as deep-check, on
  a directory graph that included very small immutable directories,
  would result in an exception causing the whole operation to abort.)
  (`#948`_)

Usability Improvements
----------------------

- Improved user interface messages and error reporting. (`#681`_,
  `#837`_, `#939`_)
- The timeouts for operation handles have been greatly increased, so
  that you can view the results of an operation up to 4 days after it
  has completed. After viewing them for the first time, the results
  are retained for a further day. (`#577`_)

Release 1.6.0 (2010-02-01)
''''''''''''''''''''''''''

New Features
------------

- Immutable Directories

  Tahoe-LAFS can now create and handle immutable
  directories. (`#607`_, `#833`_, `#931`_) These are read just like
  normal directories, but are "deep-immutable", meaning that all their
  children (and everything reachable from those children) must be
  immutable objects (i.e. immutable or literal files, and other
  immutable directories).

  These directories must be created in a single webapi call that
  provides all of the children at once. (Since they cannot be changed
  after creation, the usual create/add/add sequence cannot be used.)
  They have URIs that start with "URI:DIR2-CHK:" or "URI:DIR2-LIT:",
  and are described on the human-facing web interface (aka the "WUI")
  with a "DIR-IMM" abbreviation (as opposed to "DIR" for the usual
  read-write directories and "DIR-RO" for read-only directories).

  Tahoe-LAFS releases before 1.6.0 cannot read the contents of an
  immutable directory. 1.5.0 will tolerate their presence in a
  directory listing (and display it as "unknown"). 1.4.1 and earlier
  cannot tolerate them: a DIR-IMM child in any directory will prevent
  the listing of that directory.

  Immutable directories are repairable, just like normal immutable
  files.

  The webapi "POST t=mkdir-immutable" call is used to create immutable
  directories. See `webapi.rst`_ for details.

- "tahoe backup" now creates immutable directories, backupdb has
  dircache

  The "tahoe backup" command has been enhanced to create immutable
  directories (in previous releases, it created read-only mutable
  directories) (`#828`_). This is significantly faster, since it does
  not need to create an RSA keypair for each new directory. Also
  "DIR-IMM" immutable directories are repairable, unlike "DIR-RO"
  read-only mutable directories at present. (A future Tahoe-LAFS
  release should also be able to repair DIR-RO.)

  In addition, the backupdb (used by "tahoe backup" to remember what
  it has already copied) has been enhanced to store information about
  existing immutable directories. This allows it to re-use directories
  that have moved but still contain identical contents, or that have
  been deleted and later replaced. (The 1.5.0 "tahoe backup" command
  could only re-use directories that were in the same place as they
  were in the immediately previous backup.)  With this change, the
  backup process no longer needs to read the previous snapshot out of
  the Tahoe-LAFS grid, reducing the network load
  considerably. (`#606`_)

  A "null backup" (in which nothing has changed since the previous
  backup) will require only two Tahoe-side operations: one to add an
  Archives/$TIMESTAMP entry, and a second to update the Latest/
  link. On the local disk side, it will readdir() all your local
  directories and stat() all your local files.

  If you've been using "tahoe backup" for a while, you will notice
  that your first use of it after upgrading to 1.6.0 may take a long
  time: it must create proper immutable versions of all the old
  read-only mutable directories. This process won't take as long as
  the initial backup (where all the file contents had to be uploaded
  too): it will require time proportional to the number and size of
  your directories. After this initial pass, all subsequent passes
  should take a tiny fraction of the time.

  As noted above, Tahoe-LAFS versions earlier than 1.5.0 cannot list a
  directory containing an immutable subdirectory. Tahoe-LAFS versions
  earlier than 1.6.0 cannot read the contents of an immutable
  directory.

  The "tahoe backup" command has been improved to skip over unreadable
  objects (like device files, named pipes, and files with permissions
  that prevent the command from reading their contents), instead of
  throwing an exception and terminating the backup process. It also
  skips over symlinks, because these cannot be represented faithfully
  in the Tahoe-side filesystem. A warning message will be emitted each
  time something is skipped. (`#729`_, `#850`_, `#641`_)

- "create-node" command added, "create-client" now implies
  --no-storage

  The basic idea behind Tahoe-LAFS's client+server and client-only
  processes is that you are creating a general-purpose Tahoe-LAFS
  "node" process, which has several components that can be
  activated. Storage service is one of these optional components, as
  is the Helper, FTP server, and SFTP server. Web gateway
  functionality is nominally on this list, but it is always active; a
  future release will make it optional. There are three special
  purpose servers that can't currently be run as a component in a
  node: introducer, key-generator, and stats-gatherer.

  So now "tahoe create-node" will create a Tahoe-LAFS node process,
  and after creation you can edit its tahoe.cfg to enable or disable
  the desired services. It is a more general-purpose replacement for
  "tahoe create-client".  The default configuration has storage
  service enabled. For convenience, the "--no-storage" argument makes
  a tahoe.cfg file that disables storage service. (`#760`_)

  "tahoe create-client" has been changed to create a Tahoe-LAFS node
  without a storage service. It is equivalent to "tahoe create-node
  --no-storage". This helps to reduce the confusion surrounding the
  use of a command with "client" in its name to create a storage
  *server*. Use "tahoe create-client" to create a purely client-side
  node. If you want to offer storage to the grid, use "tahoe
  create-node" instead.

  In the future, other services will be added to the node, and they
  will be controlled through options in tahoe.cfg . The most important
  of these services may get additional --enable-XYZ or --disable-XYZ
  arguments to "tahoe create-node".

- Performance Improvements

  Download of immutable files begins as soon as the downloader has
  located the K necessary shares (`#928`_, `#287`_). In both the
  previous and current releases, a downloader will first issue queries
  to all storage servers on the grid to locate shares before it begins
  downloading the shares. In previous releases of Tahoe-LAFS, download
  would not begin until all storage servers on the grid had replied to
  the query, at which point K shares would be chosen for download from
  among the shares that were located. In this release, download begins
  as soon as any K shares are located. This means that downloads start
  sooner, which is particularly important if there is a server on the
  grid that is extremely slow or even hung in such a way that it will
  never respond. In previous releases such a server would have a
  negative impact on all downloads from that grid. In this release,
  such a server will have no impact on downloads, as long as K shares
  can be found on other, quicker, servers.  This also means that
  downloads now use the "best-alacrity" servers that they talk to, as
  measured by how quickly the servers reply to the initial query. This
  might cause downloads to go faster, especially on grids with
  heterogeneous servers or geographical dispersion.

Minor Changes
-------------

- The webapi acquired a new "t=mkdir-with-children" command, to create
  and populate a directory in a single call. This is significantly
  faster than using separate "t=mkdir" and "t=set-children" operations
  (it uses one gateway-to-grid roundtrip, instead of three or
  four). (`#533`_)

- The t=set-children (note the hyphen) operation is now documented in
  webapi.rst, and is the new preferred spelling of the
  old t=set_children (with an underscore). The underscore version
  remains for backwards compatibility. (`#381`_, `#927`_)

- The tracebacks produced by errors in CLI tools should now be in
  plain text, instead of HTML (which is unreadable outside of a
  browser). (`#646`_)

- The [storage]reserved_space configuration knob (which causes the
  storage server to refuse shares when available disk space drops
  below a threshold) should work on Windows now, not just
  UNIX. (`#637`_)

- "tahoe cp" should now exit with status "1" if it cannot figure out a
  suitable target filename, such as when you copy from a bare
  filecap. (`#761`_)

- "tahoe get" no longer creates a zero-length file upon
  error. (`#121`_)

- "tahoe ls" can now list single files. (`#457`_)

- "tahoe deep-check --repair" should tolerate repair failures now,
  instead of halting traversal. (`#874`_, `#786`_)

- "tahoe create-alias" no longer corrupts the aliases file if it had
  previously been edited to have no trailing newline. (`#741`_)

- Many small packaging improvements were made to facilitate the
  "tahoe-lafs" package being included in Ubuntu. Several mac/win32
  binary libraries were removed, some figleaf code-coverage files were
  removed, a bundled copy of darcsver-1.2.1 was removed, and
  additional licensing text was added.

- Several DeprecationWarnings for python2.6 were silenced. (`#859`_)

- The checker --add-lease option would sometimes fail for shares
  stored on old (Tahoe v1.2.0) servers. (`#875`_)

- The documentation for installing on Windows (docs/quickstart.rst)
  has been improved. (`#773`_)

For other changes not mentioned here, see
<https://tahoe-lafs.org/trac/tahoe-lafs/query?milestone=1.6.0&keywords=!~news-done>.
To include the tickets mentioned above, go to
<https://tahoe-lafs.org/trac/tahoe-lafs/query?milestone=1.6.0>.

.. _`#121`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/121
.. _`#287`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/287
.. _`#381`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/381
.. _`#457`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/457
.. _`#533`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/533
.. _`#577`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/577
.. _`#606`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/606
.. _`#607`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/607
.. _`#637`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/637
.. _`#641`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/641
.. _`#646`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/646
.. _`#681`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/681
.. _`#729`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/729
.. _`#741`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/741
.. _`#760`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/760
.. _`#761`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/761
.. _`#773`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/773
.. _`#786`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/786
.. _`#828`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/828
.. _`#833`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/833
.. _`#859`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/859
.. _`#874`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/874
.. _`#875`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/875
.. _`#931`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/931
.. _`#837`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/837
.. _`#850`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/850
.. _`#927`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/927
.. _`#928`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/928
.. _`#939`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/939
.. _`#948`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/948
.. _webapi.rst: docs/frontends/webapi.rst

Release 1.5.0 (2009-08-01)
''''''''''''''''''''''''''

Improvements
------------

- Uploads of immutable files now use pipelined writes, improving
  upload speed slightly (10%) over high-latency connections. (`#392`_)

- Processing large directories has been sped up, by removing a O(N^2)
  algorithm from the dirnode decoding path and retaining unmodified
  encrypted entries.  (`#750`_, `#752`_)

- The human-facing web interface (aka the "WUI") received a
  significant CSS makeover by Kevin Reid, making it much prettier and
  easier to read. The WUI "check" and "deep-check" forms now include a
  "Renew Lease" checkbox, mirroring the CLI --add-lease option, so
  leases can be added or renewed from the web interface.

- The CLI "tahoe mv" command now refuses to overwrite
  directories. (`#705`_)

- The CLI "tahoe webopen" command, when run without arguments, will
  now bring up the "Welcome Page" (node status and mkdir/upload
  forms).

- The 3.5MB limit on mutable files was removed, so it should be
  possible to upload arbitrarily-sized mutable files. Note, however,
  that the data format and algorithm remains the same, so using
  mutable files still requires bandwidth, computation, and RAM in
  proportion to the size of the mutable file.  (`#694`_)

- This version of Tahoe-LAFS will tolerate directory entries that
  contain filecap formats which it does not recognize: files and
  directories from the future.  This should improve the user
  experience (for 1.5.0 users) when we add new cap formats in the
  future. Previous versions would fail badly, preventing the user from
  seeing or editing anything else in those directories. These
  unrecognized objects can be renamed and deleted, but obviously not
  read or written. Also they cannot generally be copied. (`#683`_)

Bugfixes
--------

- deep-check-and-repair now tolerates read-only directories, such as
  the ones produced by the "tahoe backup" CLI command. Read-only
  directories and mutable files are checked, but not
  repaired. Previous versions threw an exception when attempting the
  repair and failed to process the remaining contents. We cannot yet
  repair these read-only objects, but at least this version allows the
  rest of the check+repair to proceed. (`#625`_)

- A bug in 1.4.1 which caused a server to be listed multiple times
  (and frequently broke all connections to that server) was
  fixed. (`#653`_)

- The plaintext-hashing code was removed from the Helper interface,
  removing the Helper's ability to mount a
  partial-information-guessing attack. (`#722`_)

Platform/packaging changes
--------------------------

- Tahoe-LAFS now runs on NetBSD, OpenBSD, ArchLinux, and NixOS, and on
  an embedded system based on an ARM CPU running at 266 MHz.

- Unit test timeouts have been raised to allow the tests to complete
  on extremely slow platforms like embedded ARM-based NAS boxes, which
  may take several hours to run the test suite. An ARM-specific
  data-corrupting bug in an older version of Crypto++ (5.5.2) was
  identified: ARM-users are encouraged to use recent
  Crypto++/pycryptopp which avoids this problem.

- Tahoe-LAFS now requires a SQLite library, either the sqlite3 that
  comes built-in with python2.5/2.6, or the add-on pysqlite2 if you're
  using python2.4. In the previous release, this was only needed for
  the "tahoe backup" command: now it is mandatory.

- Several minor documentation updates were made.

- To help get Tahoe-LAFS into Linux distributions like Fedora and
  Debian, packaging improvements are being made in both Tahoe-LAFS and
  related libraries like pycryptopp and zfec.

- The Crypto++ library included in the pycryptopp package has been
  upgraded to version 5.6.0 of Crypto++, which includes a more
  efficient implementation of SHA-256 in assembly for x86 or amd64
  architectures.

dependency updates
------------------

- foolscap-0.4.1
- no python-2.4.0 or 2.4.1 (2.4.2 is good) (they contained a bug in base64.b32decode)
- avoid python-2.6 on windows with mingw: compiler issues
- python2.4 requires pysqlite2 (2.5,2.6 does not)
- no python-3.x
- pycryptopp-0.5.15

.. _#392: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/392
.. _#625: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/625
.. _#653: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/653
.. _#683: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/683
.. _#694: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/694
.. _#705: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/705
.. _#722: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/722
.. _#750: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/750
.. _#752: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/752

Release 1.4.1 (2009-04-13)
''''''''''''''''''''''''''

Garbage Collection
------------------

- The big feature for this release is the implementation of garbage
  collection, allowing Tahoe storage servers to delete shares for old
  deleted files. When enabled, this uses a "mark and sweep" process:
  clients are responsible for updating the leases on their shares
  (generally by running "tahoe deep-check --add-lease"), and servers
  are allowed to delete any share which does not have an up-to-date
  lease. The process is described in detail in
  `garbage-collection.rst`_.

  The server must be configured to enable garbage-collection, by
  adding directives to the [storage] section that define an age limit
  for shares. The default configuration will not delete any shares.

  Both servers and clients should be upgraded to this release to make
  the garbage-collection as pleasant as possible. 1.2.0 servers have
  code to perform the update-lease operation but it suffers from a
  fatal bug, while 1.3.0 servers have update-lease but will return an
  exception for unknown storage indices, causing clients to emit an
  Incident for each exception, slowing the add-lease process down to a
  crawl. 1.1.0 servers did not have the add-lease operation at all.

Security/Usability Problems Fixed
---------------------------------

- A super-linear algorithm in the Merkle Tree code was fixed, which
  previously caused e.g. download of a 10GB file to take several hours
  before the first byte of plaintext could be produced. The new
  "alacrity" is about 2 minutes. A future release should reduce this
  to a few seconds by fixing ticket `#442`_.

- The previous version permitted a small timing attack (due to our use
  of strcmp) against the write-enabler and lease-renewal/cancel
  secrets. An attacker who could measure response-time variations of
  approximatly 3ns against a very noisy background time of about 15ms
  might be able to guess these secrets. We do not believe this attack
  was actually feasible. This release closes the attack by first
  hashing the two strings to be compared with a random secret.

webapi changes
--------------

- In most cases, HTML tracebacks will only be sent if an "Accept:
  text/html" header was provided with the HTTP request. This will
  generally cause browsers to get an HTMLized traceback but send
  regular text/plain tracebacks to non-browsers (like the CLI
  clients). More errors have been mapped to useful HTTP error codes.

- The streaming webapi operations (deep-check and manifest) now have a
  way to indicate errors (an output line that starts with "ERROR"
  instead of being legal JSON). See `webapi.rst`_ for
  details.

- The storage server now has its own status page (at /storage), linked
  from the Welcome page. This page shows progress and results of the
  two new share-crawlers: one which merely counts shares (to give an
  estimate of how many files/directories are being stored in the
  grid), the other examines leases and reports how much space would be
  freed if GC were enabled. The page also shows how much disk space is
  present, used, reserved, and available for the Tahoe server, and
  whether the server is currently running in "read-write" mode or
  "read-only" mode.

- When a directory node cannot be read (perhaps because of insufficent
  shares), a minimal webapi page is created so that the "more-info"
  links (including a Check/Repair operation) will still be accessible.

- A new "reliability" page was added, with the beginnings of work on a
  statistical loss model. You can tell this page how many servers you
  are using and their independent failure probabilities, and it will
  tell you the likelihood that an arbitrary file will survive each
  repair period. The "numpy" package must be installed to access this
  page. A partial paper, written by Shawn Willden, has been added to
  docs/proposed/lossmodel.lyx .

CLI changes
-----------

- "tahoe check" and "tahoe deep-check" now accept an "--add-lease"
  argument, to update a lease on all shares. This is the "mark" side
  of garbage collection.

- In many cases, CLI error messages have been improved: the ugly
  HTMLized traceback has been replaced by a normal python traceback.

- "tahoe deep-check" and "tahoe manifest" now have better error
  reporting.  "tahoe cp" is now non-verbose by default.

- "tahoe backup" now accepts several "--exclude" arguments, to ignore
  certain files (like editor temporary files and version-control
  metadata) during backup.

- On windows, the CLI now accepts local paths like "c:\dir\file.txt",
  which previously was interpreted as a Tahoe path using a "c:" alias.

- The "tahoe restart" command now uses "--force" by default (meaning
  it will start a node even if it didn't look like there was one
  already running).

- The "tahoe debug consolidate" command was added. This takes a series
  of independent timestamped snapshot directories (such as those
  created by the allmydata.com windows backup program, or a series of
  "tahoe cp -r" commands) and creates new snapshots that used shared
  read-only directories whenever possible (like the output of "tahoe
  backup"). In the most common case (when the snapshots are fairly
  similar), the result will use significantly fewer directories than
  the original, allowing "deep-check" and similar tools to run much
  faster. In some cases, the speedup can be an order of magnitude or
  more.  This tool is still somewhat experimental, and only needs to
  be run on large backups produced by something other than "tahoe
  backup", so it was placed under the "debug" category.

- "tahoe cp -r --caps-only tahoe:dir localdir" is a diagnostic tool
  which, instead of copying the full contents of files into the local
  directory, merely copies their filecaps. This can be used to verify
  the results of a "consolidation" operation.

other fixes
-----------

- The codebase no longer rauses RuntimeError as a kind of
  assert(). Specific exception classes were created for each previous
  instance of RuntimeError.

- Many unit tests were changed to use a non-network test harness,
  speeding them up considerably.

- Deep-traversal operations (manifest and deep-check) now walk
  individual directories in alphabetical order. Occasional turn breaks
  are inserted to prevent a stack overflow when traversing directories
  with hundreds of entries.

- The experimental SFTP server had its path-handling logic changed
  slightly, to accomodate more SFTP clients, although there are still
  issues (`#645`_).

.. _#442: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/442
.. _#645: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/645
.. _garbage-collection.rst: docs/garbage-collection.rst

Release 1.3.0 (2009-02-13)
''''''''''''''''''''''''''

Checker/Verifier/Repairer
-------------------------

- The primary focus of this release has been writing a checker /
  verifier / repairer for files and directories.  "Checking" is the
  act of asking storage servers whether they have a share for the
  given file or directory: if there are not enough shares available,
  the file or directory will be unrecoverable. "Verifying" is the act
  of downloading and cryptographically asserting that the server's
  share is undamaged: it requires more work (bandwidth and CPU) than
  checking, but can catch problems that simple checking
  cannot. "Repair" is the act of replacing missing or damaged shares
  with new ones.

- This release includes a full checker, a partial verifier, and a
  partial repairer. The repairer is able to handle missing shares: new
  shares are generated and uploaded to make up for the missing
  ones. This is currently the best application of the repairer: to
  replace shares that were lost because of server departure or
  permanent drive failure.

- The repairer in this release is somewhat able to handle corrupted
  shares. The limitations are:

 - Immutable verifier is incomplete: not all shares are used, and not
   all fields of those shares are verified. Therefore the immutable
   verifier has only a moderate chance of detecting corrupted shares.
 - The mutable verifier is mostly complete: all shares are examined,
   and most fields of the shares are validated.
 - The storage server protocol offers no way for the repairer to
   replace or delete immutable shares. If corruption is detected, the
   repairer will upload replacement shares to other servers, but the
   corrupted shares will be left in place.
 - read-only directories and read-only mutable files must be repaired
   by someone who holds the write-cap: the read-cap is
   insufficient. Moreover, the deep-check-and-repair operation will
   halt with an error if it attempts to repair one of these read-only
   objects.
 - Some forms of corruption can cause both download and repair
   operations to fail. A future release will fix this, since download
   should be tolerant of any corruption as long as there are at least
   'k' valid shares, and repair should be able to fix any file that is
   downloadable.

- If the downloader, verifier, or repairer detects share corruption,
  the servers which provided the bad shares will be notified (via a
  file placed in the BASEDIR/storage/corruption-advisories directory)
  so their operators can manually delete the corrupted shares and
  investigate the problem. In addition, the "incident gatherer"
  mechanism will automatically report share corruption to an incident
  gatherer service, if one is configured. Note that corrupted shares
  indicate hardware failures, serious software bugs, or malice on the
  part of the storage server operator, so a corrupted share should be
  considered highly unusual.

- By periodically checking/repairing all files and directories,
  objects in the Tahoe filesystem remain resistant to recoverability
  failures due to missing and/or broken servers.

- This release includes a wapi mechanism to initiate checks on
  individual files and directories (with or without verification, and
  with or without automatic repair). A related mechanism is used to
  initiate a "deep-check" on a directory: recursively traversing the
  directory and its children, checking (and/or verifying/repairing)
  everything underneath. Both mechanisms can be run with an
  "output=JSON" argument, to obtain machine-readable check/repair
  status results. These results include a copy of the filesystem
  statistics from the "deep-stats" operation (including total number
  of files, size histogram, etc). If repair is possible, a "Repair"
  button will appear on the results page.

- The client web interface now features some extra buttons to initiate
  check and deep-check operations. When these operations finish, they
  display a results page that summarizes any problems that were
  encountered. All long-running deep-traversal operations, including
  deep-check, use a start-and-poll mechanism, to avoid depending upon
  a single long-lived HTTP connection. `webapi.rst`_ has
  details.

Efficient Backup
----------------

- The "tahoe backup" command is new in this release, which creates
  efficient versioned backups of a local directory. Given a local
  pathname and a target Tahoe directory, this will create a read-only
  snapshot of the local directory in $target/Archives/$timestamp. It
  will also create $target/Latest, which is a reference to the latest
  such snapshot. Each time you run "tahoe backup" with the same source
  and target, a new $timestamp snapshot will be added. These snapshots
  will share directories that have not changed since the last backup,
  to speed up the process and minimize storage requirements. In
  addition, a small database is used to keep track of which local
  files have been uploaded already, to avoid uploading them a second
  time. This drastically reduces the work needed to do a "null backup"
  (when nothing has changed locally), making "tahoe backup' suitable
  to run from a daily cronjob.

  Note that the "tahoe backup" CLI command must be used in conjunction
  with a 1.3.0-or-newer Tahoe client node; there was a bug in the
  1.2.0 webapi implementation that would prevent the last step (create
  $target/Latest) from working.

Large Files
-----------

- The 12GiB (approximate) immutable-file-size limitation is
  lifted. This release knows how to handle so-called "v2 immutable
  shares", which permit immutable files of up to about 18 EiB (about
  3*10^14). These v2 shares are created if the file to be uploaded is
  too large to fit into v1 shares. v1 shares are created if the file
  is small enough to fit into them, so that files created with
  tahoe-1.3.0 can still be read by earlier versions if they are not
  too large. Note that storage servers also had to be changed to
  support larger files, and this release is the first release in which
  they are able to do that. Clients will detect which servers are
  capable of supporting large files on upload and will not attempt to
  upload shares of a large file to a server which doesn't support it.

FTP/SFTP Server
---------------

- Tahoe now includes experimental FTP and SFTP servers. When
  configured with a suitable method to translate username+password
  into a root directory cap, it provides simple access to the virtual
  filesystem. Remember that FTP is completely unencrypted: passwords,
  filenames, and file contents are all sent over the wire in
  cleartext, so FTP should only be used on a local (127.0.0.1)
  connection. This feature is still in development: there are no unit
  tests yet, and behavior with respect to Unicode filenames is
  uncertain. Please see `FTP-and-SFTP.rst`_ for
  configuration details. (`#512`_, `#531`_)

CLI Changes
-----------

- This release adds the 'tahoe create-alias' command, which is a
  combination of 'tahoe mkdir' and 'tahoe add-alias'. This also allows
  you to start using a new tahoe directory without exposing its URI in
  the argv list, which is publicly visible (through the process table)
  on most unix systems.  Thanks to Kevin Reid for bringing this issue
  to our attention.

- The single-argument form of "tahoe put" was changed to create an
  unlinked file. I.e. "tahoe put bar.txt" will take the contents of a
  local "bar.txt" file, upload them to the grid, and print the
  resulting read-cap; the file will not be attached to any
  directories. This seemed a bit more useful than the previous
  behavior (copy stdin, upload to the grid, attach the resulting file
  into your default tahoe: alias in a child named 'bar.txt').

- "tahoe put" was also fixed to handle mutable files correctly: "tahoe
  put bar.txt URI:SSK:..." will read the contents of the local bar.txt
  and use them to replace the contents of the given mutable file.

- The "tahoe webopen" command was modified to accept aliases. This
  means "tahoe webopen tahoe:" will cause your web browser to open to
  a "wui" page that gives access to the directory associated with the
  default "tahoe:" alias. It should also accept leading slashes, like
  "tahoe webopen tahoe:/stuff".

- Many esoteric debugging commands were moved down into a "debug"
  subcommand:

 - tahoe debug dump-cap
 - tahoe debug dump-share
 - tahoe debug find-shares
 - tahoe debug catalog-shares
 - tahoe debug corrupt-share

   The last command ("tahoe debug corrupt-share") flips a random bit
   of the given local sharefile. This is used to test the file
   verifying/repairing code, and obviously should not be used on user
   data.

The cli might not correctly handle arguments which contain non-ascii
characters in Tahoe v1.3 (although depending on your platform it
might, especially if your platform can be configured to pass such
characters on the command-line in utf-8 encoding).  See
https://tahoe-lafs.org/trac/tahoe-lafs/ticket/565 for details.

Web changes
-----------

- The "default webapi port", used when creating a new client node (and
  in the getting-started documentation), was changed from 8123 to
  3456, to reduce confusion when Tahoe accessed through a Firefox
  browser on which the "Torbutton" extension has been installed. Port
  8123 is occasionally used as a Tor control port, so Torbutton adds
  8123 to Firefox's list of "banned ports" to avoid CSRF attacks
  against Tor. Once 8123 is banned, it is difficult to diagnose why
  you can no longer reach a Tahoe node, so the Tahoe default was
  changed. Note that 3456 is reserved by IANA for the "vat" protocol,
  but there are argueably more Torbutton+Tahoe users than vat users
  these days. Note that this will only affect newly-created client
  nodes. Pre-existing client nodes, created by earlier versions of
  tahoe, may still be listening on 8123.

- All deep-traversal operations (start-manifest, start-deep-size,
  start-deep-stats, start-deep-check) now use a start-and-poll
  approach, instead of using a single (fragile) long-running
  synchronous HTTP connection. All these "start-" operations use POST
  instead of GET. The old "GET manifest", "GET deep-size", and "POST
  deep-check" operations have been removed.

- The new "POST start-manifest" operation, when it finally completes,
  results in a table of (path,cap), instead of the list of verifycaps
  produced by the old "GET manifest". The table is available in
  several formats: use output=html, output=text, or output=json to
  choose one. The JSON output also includes stats, and a list of
  verifycaps and storage-index strings. The "return_to=" and
  "when_done=" arguments have been removed from the t=check and
  deep-check operations.

- The top-level status page (/status) now has a machine-readable form,
  via "/status/?t=json". This includes information about the
  currently-active uploads and downloads, which may be useful for
  frontends that wish to display progress information. There is no
  easy way to correlate the activities displayed here with recent wapi
  requests, however.

- Any files in BASEDIR/public_html/ (configurable) will be served in
  response to requests in the /static/ portion of the URL space. This
  will simplify the deployment of javascript-based frontends that can
  still access wapi calls by conforming to the (regrettable)
  "same-origin policy".

- The welcome page now has a "Report Incident" button, which is tied
  into the "Incident Gatherer" machinery. If the node is attached to
  an incident gatherer (via log_gatherer.furl), then pushing this
  button will cause an Incident to be signalled: this means recent log
  events are aggregated and sent in a bundle to the gatherer. The user
  can push this button after something strange takes place (and they
  can provide a short message to go along with it), and the relevant
  data will be delivered to a centralized incident-gatherer for later
  processing by operations staff.

- The "HEAD" method should now work correctly, in addition to the
  usual "GET", "PUT", and "POST" methods. "HEAD" is supposed to return
  exactly the same headers as "GET" would, but without any of the
  actual response body data. For mutable files, this now does a brief
  mapupdate (to figure out the size of the file that would be
  returned), without actually retrieving the file's contents.

- The "GET" operation on files can now support the HTTP "Range:"
  header, allowing requests for partial content. This allows certain
  media players to correctly stream audio and movies out of a Tahoe
  grid. The current implementation uses a disk-based cache in
  BASEDIR/private/cache/download , which holds the plaintext of the
  files being downloaded. Future implementations might not use this
  cache. GET for immutable files now returns an ETag header.

- Each file and directory now has a "Show More Info" web page, which
  contains much of the information that was crammed into the directory
  page before. This includes readonly URIs, storage index strings,
  object type, buttons to control checking/verifying/repairing, and
  deep-check/deep-stats buttons (for directories). For mutable files,
  the "replace contents" upload form has been moved here too. As a
  result, the directory page is now much simpler and cleaner, and
  several potentially-misleading links (like t=uri) are now gone.

- Slashes are discouraged in Tahoe file/directory names, since they
  cause problems when accessing the filesystem through the
  wapi. However, there are a couple of accidental ways to generate
  such names. This release tries to make it easier to correct such
  mistakes by escaping slashes in several places, allowing slashes in
  the t=info and t=delete commands, and in the source (but not the
  target) of a t=rename command.

Packaging
---------

- Tahoe's dependencies have been extended to require the
  "[secure_connections]" feature from Foolscap, which will cause
  pyOpenSSL to be required and/or installed. If OpenSSL and its
  development headers are already installed on your system, this can
  occur automatically. Tahoe now uses pollreactor (instead of the
  default selectreactor) to work around a bug between pyOpenSSL and
  the most recent release of Twisted (8.1.0). This bug only affects
  unit tests (hang during shutdown), and should not impact regular
  use.

- The Tahoe source code tarballs now come in two different forms:
  regular and "sumo". The regular tarball contains just Tahoe, nothing
  else. When building from the regular tarball, the build process will
  download any unmet dependencies from the internet (starting with the
  index at PyPI) so it can build and install them. The "sumo" tarball
  contains copies of all the libraries that Tahoe requires (foolscap,
  twisted, zfec, etc), so using the "sumo" tarball should not require
  any internet access during the build process. This can be useful if
  you want to build Tahoe while on an airplane, a desert island, or
  other bandwidth-limited environments.

- Similarly, tahoe-lafs.org now hosts a "tahoe-deps" tarball which
  contains the latest versions of all these dependencies. This
  tarball, located at
  https://tahoe-lafs.org/source/tahoe/deps/tahoe-deps.tar.gz, can be
  unpacked in the tahoe source tree (or in its parent directory), and
  the build process should satisfy its downloading needs from it
  instead of reaching out to PyPI.  This can be useful if you want to
  build Tahoe from a darcs checkout while on that airplane or desert
  island.

- Because of the previous two changes ("sumo" tarballs and the
  "tahoe-deps" bundle), most of the files have been removed from
  misc/dependencies/ . This brings the regular Tahoe tarball down to
  2MB (compressed), and the darcs checkout (without history) to about
  7.6MB. A full darcs checkout will still be fairly large (because of
  the historical patches which included the dependent libraries), but
  a 'lazy' one should now be small.

- The default "make" target is now an alias for "setup.py build",
  which itself is an alias for "setup.py develop --prefix support",
  with some extra work before and after (see setup.cfg). Most of the
  complicated platform-dependent code in the Makefile was rewritten in
  Python and moved into setup.py, simplifying things considerably.

- Likewise, the "make test" target now delegates most of its work to
  "setup.py test", which takes care of getting PYTHONPATH configured
  to access the tahoe code (and dependencies) that gets put in
  support/lib/ by the build_tahoe step. This should allow unit tests
  to be run even when trial (which is part of Twisted) wasn't already
  installed (in this case, trial gets installed to support/bin because
  Twisted is a dependency of Tahoe).

- Tahoe is now compatible with the recently-released Python 2.6 ,
  although it is recommended to use Tahoe on Python 2.5, on which it
  has received more thorough testing and deployment.

- Tahoe is now compatible with simplejson-2.0.x . The previous release
  assumed that simplejson.loads always returned unicode strings, which
  is no longer the case in 2.0.x .

Grid Management Tools
---------------------

- Several tools have been added or updated in the misc/ directory,
  mostly munin plugins that can be used to monitor a storage grid.

 - The misc/spacetime/ directory contains a "disk watcher" daemon
   (startable with 'tahoe start'), which can be configured with a set
   of HTTP URLs (pointing at the wapi '/statistics' page of a bunch of
   storage servers), and will periodically fetch
   disk-used/disk-available information from all the servers. It keeps
   this information in an Axiom database (a sqlite-based library
   available from divmod.org). The daemon computes time-averaged rates
   of disk usage, as well as a prediction of how much time is left
   before the grid is completely full.

 - The misc/munin/ directory contains a new set of munin plugins
   (tahoe_diskleft, tahoe_diskusage, tahoe_doomsday) which talk to the
   disk-watcher and provide graphs of its calculations.

 - To support the disk-watcher, the Tahoe statistics component
   (visible through the wapi at the /statistics/ URL) now includes
   disk-used and disk-available information. Both are derived through
   an equivalent of the unix 'df' command (i.e. they ask the kernel
   for the number of free blocks on the partition that encloses the
   BASEDIR/storage directory). In the future, the disk-available
   number will be further influenced by the local storage policy: if
   that policy says that the server should refuse new shares when less
   than 5GB is left on the partition, then "disk-available" will
   report zero even though the kernel sees 5GB remaining.

 - The 'tahoe_overhead' munin plugin interacts with an
   allmydata.com-specific server which reports the total of the
   'deep-size' reports for all active user accounts, compares this
   with the disk-watcher data, to report on overhead percentages. This
   provides information on how much space could be recovered once
   Tahoe implements some form of garbage collection.

Configuration Changes: single INI-format tahoe.cfg file
-------------------------------------------------------

- The Tahoe node is now configured with a single INI-format file,
  named "tahoe.cfg", in the node's base directory. Most of the
  previous multiple-separate-files are still read for backwards
  compatibility (the embedded SSH debug server and the
  advertised_ip_addresses files are the exceptions), but new
  directives will only be added to tahoe.cfg . The "tahoe
  create-client" command will create a tahoe.cfg for you, with sample
  values commented out. (ticket `#518`_)

- tahoe.cfg now has controls for the foolscap "keepalive" and
  "disconnect" timeouts (`#521`_).

- tahoe.cfg now has controls for the encoding parameters:
  "shares.needed" and "shares.total" in the "[client]" section. The
  default parameters are still 3-of-10.

- The inefficient storage 'sizelimit' control (which established an
  upper bound on the amount of space that a storage server is allowed
  to consume) has been replaced by a lightweight 'reserved_space'
  control (which establishes a lower bound on the amount of remaining
  space). The storage server will reject all writes that would cause
  the remaining disk space (as measured by a '/bin/df' equivalent) to
  drop below this value. The "[storage]reserved_space=" tahoe.cfg
  parameter controls this setting. (note that this only affects
  immutable shares: it is an outstanding bug that reserved_space does
  not prevent the allocation of new mutable shares, nor does it
  prevent the growth of existing mutable shares).

Other Changes
-------------

- Clients now declare which versions of the protocols they
  support. This is part of a new backwards-compatibility system:
  https://tahoe-lafs.org/trac/tahoe-lafs/wiki/Versioning .

- The version strings for human inspection (as displayed on the
  Welcome web page, and included in logs) now includes a platform
  identifer (frequently including a linux distribution name, processor
  architecture, etc).

- Several bugs have been fixed, including one that would cause an
  exception (in the logs) if a wapi download operation was cancelled
  (by closing the TCP connection, or pushing the "stop" button in a
  web browser).

- Tahoe now uses Foolscap "Incidents", writing an "incident report"
  file to logs/incidents/ each time something weird occurs. These
  reports are available to an "incident gatherer" through the flogtool
  command. For more details, please see the Foolscap logging
  documentation. An incident-classifying plugin function is provided
  in misc/incident-gatherer/classify_tahoe.py .

- If clients detect corruption in shares, they now automatically
  report it to the server holding that share, if it is new enough to
  accept the report.  These reports are written to files in
  BASEDIR/storage/corruption-advisories .

- The 'nickname' setting is now defined to be a UTF-8 -encoded string,
  allowing non-ascii nicknames.

- The 'tahoe start' command will now accept a --syslog argument and
  pass it through to twistd, making it easier to launch non-Tahoe
  nodes (like the cpu-watcher) and have them log to syslogd instead of
  a local file. This is useful when running a Tahoe node out of a USB
  flash drive.

- The Mac GUI in src/allmydata/gui/ has been improved.

.. _#512: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/512
.. _#518: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/518
.. _#521: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/521
.. _#531: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/531

Release 1.2.0 (2008-07-21)
''''''''''''''''''''''''''

Security
--------

- This release makes the immutable-file "ciphertext hash tree"
  mandatory.  Previous releases allowed the uploader to decide whether
  their file would have an integrity check on the ciphertext or not. A
  malicious uploader could use this to create a readcap that would
  download as one file or a different one, depending upon which shares
  the client fetched first, with no errors raised. There are other
  integrity checks on the shares themselves, preventing a storage
  server or other party from violating the integrity properties of the
  read-cap: this failure was only exploitable by the uploader who
  gives you a carefully constructed read-cap. If you download the file
  with Tahoe 1.2.0 or later, you will not be vulnerable to this
  problem. `#491`_

  This change does not introduce a compatibility issue, because all
  existing versions of Tahoe will emit the ciphertext hash tree in
  their shares.

Dependencies
------------

- Tahoe now requires Foolscap-0.2.9 . It also requires pycryptopp 0.5
  or newer, since earlier versions had a bug that interacted with
  specific compiler versions that could sometimes result in incorrect
  encryption behavior. Both packages are included in the Tahoe source
  tarball in misc/dependencies/ , and should be built automatically
  when necessary.

Web API
-------

- Web API directory pages should now contain properly-slash-terminated
  links to other directories. They have also stopped using absolute
  links in forms and pages (which interfered with the use of a
  front-end load-balancing proxy).

- The behavior of the "Check This File" button changed, in conjunction
  with larger internal changes to file checking/verification. The
  button triggers an immediate check as before, but the outcome is
  shown on its own page, and does not get stored anywhere. As a
  result, the web directory page no longer shows historical checker
  results.

- A new "Deep-Check" button has been added, which allows a user to
  initiate a recursive check of the given directory and all files and
  directories reachable from it. This can cause quite a bit of work,
  and has no intermediate progress information or feedback about the
  process. In addition, the results of the deep-check are extremely
  limited. A later release will improve this behavior.

- The web server's behavior with respect to non-ASCII (unicode)
  filenames in the "GET save=true" operation has been improved. To
  achieve maximum compatibility with variously buggy web browsers, the
  server does not try to figure out the character set of the inbound
  filename. It just echoes the same bytes back to the browser in the
  Content-Disposition header. This seems to make both IE7 and Firefox
  work correctly.

Checker/Verifier/Repairer
-------------------------

- Tahoe is slowly acquiring convenient tools to check up on file
  health, examine existing shares for errors, and repair files that
  are not fully healthy. This release adds a mutable
  checker/verifier/repairer, although testing is very limited, and
  there are no web interfaces to trigger repair yet. The "Check"
  button next to each file or directory on the wapi page will perform
  a file check, and the "deep check" button on each directory will
  recursively check all files and directories reachable from there
  (which may take a very long time).

  Future releases will improve access to this functionality.

Operations/Packaging
--------------------

- A "check-grid" script has been added, along with a Makefile
  target. This is intended (with the help of a pre-configured node
  directory) to check upon the health of a Tahoe grid, uploading and
  downloading a few files. This can be used as a monitoring tool for a
  deployed grid, to be run periodically and to signal an error if it
  ever fails. It also helps with compatibility testing, to verify that
  the latest Tahoe code is still able to handle files created by an
  older version.

- The munin plugins from misc/munin/ are now copied into any generated
  debian packages, and are made executable (and uncompressed) so they
  can be symlinked directly from /etc/munin/plugins/ .

- Ubuntu "Hardy" was added as a supported debian platform, with a
  Makefile target to produce hardy .deb packages. Some notes have been
  added to `debian.rst`_ about building Tahoe on a debian/ubuntu
  system.

- Storage servers now measure operation rates and
  latency-per-operation, and provides results through the /statistics
  web page as well as the stats gatherer. Munin plugins have been
  added to match.

Other
-----

- Tahoe nodes now use Foolscap "incident logging" to record unusual
  events to their NODEDIR/logs/incidents/ directory. These incident
  files can be examined by Foolscap logging tools, or delivered to an
  external log-gatherer for further analysis. Note that Tahoe now
  requires Foolscap-0.2.9, since 0.2.8 had a bug that complained about
  "OSError: File exists" when trying to create the incidents/
  directory for a second time.

- If no servers are available when retrieving a mutable file (like a
  directory), the node now reports an error instead of hanging
  forever. Earlier releases would not only hang (causing the wapi
  directory listing to get stuck half-way through), but the internal
  dirnode serialization would cause all subsequent attempts to
  retrieve or modify the same directory to hang as well. `#463`_

- A minor internal exception (reported in logs/twistd.log, in the
  "stopProducing" method) was fixed, which complained about
  "self._paused_at not defined" whenever a file download was stopped
  from the web browser end.

.. _#463: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/463
.. _#491: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/491
.. _debian.rst: docs/debian.rst

Release 1.1.0 (2008-06-11)
''''''''''''''''''''''''''

CLI: new "alias" model
----------------------

- The new CLI code uses an scp/rsync -like interface, in which
  directories in the Tahoe storage grid are referenced by a
  colon-suffixed alias. The new commands look like:

 - tahoe cp local.txt tahoe:virtual.txt
 - tahoe ls work:subdir

- More functionality is available through the CLI: creating unlinked
  files and directories, recursive copy in or out of the storage grid,
  hardlinks, and retrieving the raw read- or write- caps through the
  'ls' command. Please read `CLI.rst`_ for complete details.

wapi: new pages, new commands
-----------------------------

- Several new pages were added to the web API:

 - /helper_status : to describe what a Helper is doing
 - /statistics : reports node uptime, CPU usage, other stats
 - /file : for easy file-download URLs, see `#221`_
 - /cap == /uri : future compatibility

- The localdir=/localfile= and t=download operations were
  removed. These required special configuration to enable anyways, but
  this feature was a security problem, and was mostly obviated by the
  new "cp -r" command.

- Several new options to the GET command were added:

 -  t=deep-size : add up the size of all immutable files reachable from the directory
 -  t=deep-stats : return a JSON-encoded description of number of files, size distribution, total size, etc

- POST is now preferred over PUT for most operations which cause
  side-effects.

- Most wapi calls now accept overwrite=, and default to overwrite=true

- "POST /uri/DIRCAP/parent/child?t=mkdir" is now the preferred API to
  create multiple directories at once, rather than ...?t=mkdir-p .

- PUT to a mutable file ("PUT /uri/MUTABLEFILECAP", "PUT
  /uri/DIRCAP/child") will modify the file in-place.

- more munin graphs in misc/munin/

 - tahoe-introstats
 - tahoe-rootdir-space
 - tahoe_estimate_files
 - mutable files published/retrieved
 - tahoe_cpu_watcher
 - tahoe_spacetime

New Dependencies
----------------
-  zfec 1.1.0
-  foolscap 0.2.8
-  pycryptopp 0.5
-  setuptools (now required at runtime)

New Mutable-File Code
---------------------

- The mutable-file handling code (mostly used for directories) has
  been completely rewritten. The new scheme has a better API (with a
  modify() method) and is less likely to lose data when several
  uncoordinated writers change a file at the same time.

- In addition, a single Tahoe process will coordinate its own
  writes. If you make two concurrent directory-modifying wapi calls to
  a single tahoe node, it will internally make one of them wait for
  the other to complete. This prevents auto-collision (`#391`_).

- The new mutable-file code also detects errors during publish
  better. Earlier releases might believe that a mutable file was
  published when in fact it failed.

other features
--------------

- The node now monitors its own CPU usage, as a percentage, measured
  every 60 seconds. 1/5/15 minute moving averages are available on the
  /statistics web page and via the stats-gathering interface.

- Clients now accelerate reconnection to all servers after being
  offline (`#374`_). When a client is offline for a long time, it
  scales back reconnection attempts to approximately once per hour, so
  it may take a while to make the first attempt, but once any attempt
  succeeds, the other server connections will be retried immediately.

- A new "offloaded KeyGenerator" facility can be configured, to move
  RSA key generation out from, say, a wapi node, into a separate
  process. RSA keys can take several seconds to create, and so a wapi
  node which is being used for directory creation will be unavailable
  for anything else during this time. The Key Generator process will
  pre-compute a small pool of keys, to speed things up further. This
  also takes better advantage of multi-core CPUs, or SMP hosts.

- The node will only use a potentially-slow "du -s" command at startup
  (to measure how much space has been used) if the "sizelimit"
  parameter has been configured (to limit how much space is
  used). Large storage servers should turn off sizelimit until a later
  release improves the space-management code, since "du -s" on a
  terabyte filesystem can take hours.

- The Introducer now allows new announcements to replace old ones, to
  avoid buildups of obsolete announcements.

- Immutable files are limited to about 12GiB (when using the default
  3-of-10 encoding), because larger files would be corrupted by the
  four-byte share-size field on the storage servers (`#439`_). A later
  release will remove this limit. Earlier releases would allow >12GiB
  uploads, but the resulting file would be unretrievable.

- The docs/ directory has been rearranged, with old docs put in
  docs/historical/ and not-yet-implemented ones in docs/proposed/ .

- The Mac OS-X FUSE plugin has a significant bug fix: earlier versions
  would corrupt writes that used seek() instead of writing the file in
  linear order.  The rsync tool is known to perform writes in this
  order. This has been fixed.

.. _#221: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/221
.. _#374: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/374
.. _#391: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/391
.. _#439: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/439
.. _CLI.rst: docs/CLI.rst
