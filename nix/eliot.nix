{ lib, buildPythonPackage, fetchPypi, zope_interface, pyrsistent, boltons
, hypothesis, testtools, pytest }:
buildPythonPackage rec {
  pname = "eliot";
  version = "1.7.0";

  src = fetchPypi {
    inherit pname version;
    sha256 = "0ylyycf717s5qsrx8b9n6m38vyj2k8328lfhn8y6r31824991wv8";
  };

  postPatch = ''
    substituteInPlace setup.py \
      --replace "boltons >= 19.0.1" boltons
  '';

  # A seemingly random subset of the test suite fails intermittently.  After
  # Tahoe-LAFS is ported to Python 3 we can update to a newer Eliot and, if
  # the test suite continues to fail, maybe it will be more likely that we can
  # have upstream fix it for us.
  doCheck = false;

  checkInputs = [ testtools pytest hypothesis ];
  propagatedBuildInputs = [ zope_interface pyrsistent boltons ];

  meta = with lib; {
    homepage = https://github.com/itamarst/eliot/;
    description = "Logging library that tells you why it happened";
    license = licenses.asl20;
  };
}
