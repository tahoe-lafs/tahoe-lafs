{ lib, buildPythonPackage, fetchPypi }:
buildPythonPackage rec {
  pname = "cbor2";
  version = "5.2.0";

  src = fetchPypi {
    sha256 = "1mmmncfbsx7cbdalcrsagp9hx7wqfawaz9361gjkmsk3lp6chd5w";
    inherit pname version;
  };

  doCheck = false;

  meta = with lib; {
    homepage = https://github.com/agronholm/cbor2;
    description = "CBOR encoder/decoder";
    license = licenses.mit;
  };
}
