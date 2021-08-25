******************************
Building Tahoe-LAFS on Windows
******************************

If you are looking to hack on the source code or run pre-release code, we recommend you create a virtualenv instance and install Tahoe-LAFS into that:


1. Make sure you have Powershell installed. See `PowerShell installation <https://docs.microsoft.com/en-us/powershell/scripting/install/installing-powershell-core-on-windows?view=powershell-7.1>`_.

2. Install the latest version of Python 3. Download the .exe file at the `python website <https://www.python.org/downloads/>`_.

3. Open the installer by double-clicking it. Select the **Add Python to PATH** check-box, then click **Install Now**.

4. Start PowerShell and enter the following command to verify python installation::
   
    python --version

5. Use ``pip`` to install ``virtualenv``::
   
    pip install --user virtualenv

6. Create a fresh virtualenv for your Tahoe-LAFS install using the following command::
    
     virtualenv venv

 .. note::
    venv is the name of the virtual environment in this example. Use any name for your environment.

7. Use pip to install Tahoe-LAFS in the virtualenv instance::
   
    venv\Scripts\pip install tahoe-lafs

6. Verify installation by checking for the version::
   
    venv\Scripts\tahoe --version

If you do not want to use the full path, i.e. ``venv\Scripts\tahoe`` everytime you want to run tahoe, you can:

* Activate the virtualenv::
  
   . venv\Scripts\activate
   
  This will generate a subshell with a ``$PATH`` that includes the ``venv\Scripts\`` directory.

* Change your ``$PATH`` to include the ``venv\Scripts`` directory.