{ lib, buildPythonPackage, fetchPypi }:
buildPythonPackage rec {
  pname = "klein";
  version = "21.8.0";

  src = fetchPypi {
    sha256 = "09i1x5ppan3kqsgclbz8xdnlvzvp3amijbmdzv0kik8p5l5zswxa";
    inherit pname version;
  };

  doCheck = false;

  meta = with lib; {
    homepage = https://github.com/twisted/klein;
    description = "Nicer web server for Twisted";
    license = licenses.mit;
  };
}
