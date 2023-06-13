{ pyopenssl, fetchPypi, isPyPy }:
pyopenssl.overrideAttrs (old: rec {
  pname = "pyOpenSSL";
  version = "23.2.0";
  src = fetchPypi {
    inherit pname version;
    sha256 = "J2+TH1WkUufeppxxc+mE6ypEB85BPJGKo0tV+C+bi6w=";
  };
  # Building the docs requires sphinx which brings in a dependency on babel,
  # the test suite of which fails.
  dontBuildDocs = isPyPy;
})
