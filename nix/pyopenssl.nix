{ pyopenssl, fetchPypi, isPyPy }:
pyopenssl.overrideAttrs (old: rec {
  pname = "pyOpenSSL";
  version = "23.2.0";
  name = "${pname}-${version}";
  src = fetchPypi {
    inherit pname version;
    sha256 = "J2+TH1WkUufeppxxc+mE6ypEB85BPJGKo0tV+C+bi6w=";
  };
})
