{ pyopenssl, fetchPypi, isPyPy }:
pyopenssl.overrideAttrs (old: rec {
  pname = "pyOpenSSL";
  version = "24.1.0";
  name = "${pname}-${version}";
  src = fetchPypi {
    inherit pname version;
    sha256 = "yr7Uv6pd+fGhbA72Sgy2Uxi1zQd6ftp9aXATHKL0Gm8=";
  };
})
