{ lib, buildPythonPackage, fetchPypi }:
buildPythonPackage rec {
  pname = "klein";
  version = "21.8.0";

  src = fetchPypi {
    sha256 = "1mpydmz90d0n9dwa7mr6pgj5v0kczfs05ykssrasdq368dssw7ch";
    inherit pname version;
  };

  doCheck = false;

  propagatedBuildInputs = [ attrs hyperlink incremental Tubes Twisted typing_extensions Werkzeug zope.interface ];

  meta = with lib; {
    homepage = https://github.com/twisted/klein;
    description = "Nicer web server for Twisted";
    license = licenses.mit;
  };
}
