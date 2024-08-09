****************************
Building Tahoe-LAFS on Linux
****************************

Tahoe-LAFS has made packages available for installing on many linux and BSD distributions.
Debian and Ubuntu users can use ``apt-get install tahoe-lafs``.
If you are working on a Linux distribution which does not have Tahoe-LAFS or are looking to hack on the source code, you can build Tahoe-LAFS yourself:

Prerequisites
=============

Make sure the following are installed:

* **Python 3's latest version**: Check for the version by running ``python --version``.
* **pip**: Most python installations already include ``pip``. However, if your installation does not, see `pip installation <https://pip.pypa.io/en/stable/installing/>`_.
* **virtualenv**: Use ``pip`` to install virtualenv::
  
    pip install --user virtualenv

* **C compiler and libraries**:

    * ``python-dev``: Python development headers.
    * ``libffi-dev``: Foreign Functions Interface library.
    * ``libssl-dev``: SSL library, Tahoe-LAFS needs OpenSSL version 1.1.1c or greater.
  
    .. note::
       If you are working on Debian or Ubuntu, you can install the necessary libraries using ``apt-get``::

        apt-get install python-dev libffi-dev libssl-dev 

       On an RPM-based system such as Fedora, you can install the necessary libraries using ``yum`` or ``rpm``. However, the packages may be named differently.

Install the Latest Tahoe-LAFS Release
=====================================

If you are looking to hack on the source code or run pre-release code, we recommend you install Tahoe-LAFS directly from source by creating a ``virtualenv`` instance:

1. Clone the Tahoe-LAFS repository::
   
    git clone https://github.com/tahoe-lafs/tahoe-lafs.git

2. Move into the tahoe-lafs directory::
      
    cd tahoe-lafs

3. Create a fresh virtualenv for your Tahoe-LAFS install::
   
    virtualenv venv 

.. note::
   venv is the name of the virtual environment in this example. Use any name for your environment.

4. Upgrade ``pip`` and ``setuptools`` on the newly created virtual environment::
   
    venv/bin/pip install -U pip setuptools

5. If you'd like to modify the Tahoe source code, you need to install Tahoe-LAFS with the ``--editable`` flag with the ``test`` extra::
   
    venv/bin/pip install --editable .[test]

.. note::
   Tahoe-LAFS provides extra functionality when requested explicitly at installation using the "extras" feature of setuptools. To learn more about the extras which Tahoe supports, see Tahoe extras.

6. Verify installation by checking for the version::
   
    venv/bin/tahoe --version

If you do not want to use the full path, i.e., ``venv/bin/tahoe`` everytime you want to run tahoe, you can activate the ``virtualenv``::
  
   . venv/bin/activate

  This will generate a subshell with a ``$PATH`` that includes the ``venv/bin/`` directory.



