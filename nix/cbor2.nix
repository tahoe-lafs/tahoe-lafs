{ lib, buildPythonPackage, fetchPypi }:
buildPythonPackage rec {
  pname = "cbor2";
  version = "5.2.0";

  src = fetchPypi {
    sha256 = "1gwlgjl70vlv35cgkcw3cg7b5qsmws36hs4mmh0l9msgagjs4fm3";
    inherit pname version;
  };

  doCheck = false;

  meta = with lib; {
    homepage = https://github.com/agronholm/cbor2;
    description = "CBOR encoder/decoder";
    license = licenses.mit;
  };
}
