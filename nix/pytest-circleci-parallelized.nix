{ fetchPypi, buildPythonPackage, pytest }:
buildPythonPackage rec {
  pname = "pytest-circleci-parallelized";
  version = "0.1.0";
  src = fetchPypi {
    inherit pname version;
    hash = "sha256-fVkjp41hJyu2Zfr6NwgESzipRsdn7o7zQs4Icont/5I=";
  };
  propagatedBuildInputs = [ pytest ];
}
