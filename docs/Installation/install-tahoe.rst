.. -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-

..
    note: if you aren't reading the rendered form of these docs at
    http://tahoe-lafs.readthedocs.io/en/latest/ , then be aware that any
    ":doc:" links refer to other files in this docs/ directory

*********************
Installing Tahoe-LAFS
*********************

`Tahoe-LAFS`_ is a secure, decentralized, and fault-tolerant storage system.
To see an overview of the architecture and security properties, see :doc:`Welcome to Tahoe LAFS! <../about-tahoe>`

Tahoe-LAFS can be installed and used on any of the following operating systems.

.. _Tahoe-LAFS: https://tahoe-lafs.org

Microsoft Windows
=================

To install Tahoe-LAFS on Windows:

1. Make sure you have Powershell installed. See `PowerShell installation <https://docs.microsoft.com/en-us/powershell/scripting/install/installing-powershell-core-on-windows?view=powershell-7.1>`_.

2. Install the latest version of Python 3. Download the .exe file at the `python website <https://www.python.org/downloads/>`_.

3. Open the installer by double-clicking it. Select the **Add Python to PATH** check-box, then click **Install Now**.

4. Start PowerShell and enter the following command to verify python installation::

    python --version

5. Enter the following command to install Tahoe-LAFS::

    pip install tahoe-lafs

6. Verify installation by checking for the version::

    tahoe --version

If you want to hack on Tahoe's source code, you can install Tahoe in a ``virtualenv`` on your Windows Machine. To learn more, see :doc:`install-on-windows`.

Linux, BSD, or MacOS
====================

Tahoe-LAFS can be installed on MacOS, many Linux and BSD distributions. If you are using Ubuntu or Debian, run the following command to install Tahoe-LAFS::

 apt-get install tahoe-lafs

If you are working on MacOS or a Linux distribution which does not have Tahoe-LAFS packages, you can build it yourself:

1. Make sure the following are installed:

   * **Python 3's latest version**: Check for the version by running ``python --version``.
   * **pip**: Most python installations already include `pip`. However, if your installation does not, see `pip installation <https://pip.pypa.io/en/stable/installing/>`_.

2. Install Tahoe-LAFS using pip::

    pip install tahoe-lafs

3. Verify installation by checking for the version::

    tahoe --version

If you are looking to hack on the source code or run pre-release code, we recommend you install Tahoe-LAFS on a `virtualenv` instance. To learn more, see :doc:`install-on-linux`.

You can always write to the `tahoe-dev mailing list <https://lists.tahoe-lafs.org/mailman/listinfo/tahoe-dev>`_ or chat on the `Libera.chat IRC <irc://irc.libera.chat/%23tahoe-lafs>`_ if you are not able to get Tahoe-LAFS up and running on your deployment.
