{ pyopenssl, fetchPypi, isPyPy }:
pyopenssl.overrideAttrs (old: rec {
  pname = "pyopenssl";
  version = "23.2.0";
  src = fetchPypi {
    inherit pname version;
    sha256 = "sha256-1qgarxcmlrrrlyjnsry47lz04z8bviy7rrlbbp9874kdj799rckc";
  };
  # Building the docs requires sphinx which brings in a dependency on babel,
  # the test suite of which fails.
  dontBuildDocs = isPyPy;
})
