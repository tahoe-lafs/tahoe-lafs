{ lib, buildPythonPackage, fetchPypi }:
buildPythonPackage rec {
  pname = "collections-extended";
  version = "1.0.3";

  src = fetchPypi {
    inherit pname version;
    sha256 = "0lb69x23asd68n0dgw6lzxfclavrp2764xsnh45jm97njdplznkw";
  };

  # Tests aren't in tarball, for 1.0.3 at least.
  doCheck = false;

  meta = with lib; {
    homepage = https://github.com/mlenzen/collections-extended;
    description = "Extra Python Collections - bags (multisets), setlists (unique list / indexed set), RangeMap and IndexedDict";
    license = licenses.asl20;
  };
}
