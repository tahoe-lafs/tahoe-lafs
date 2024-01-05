let
  pname = "tahoe-lafs";
  version = "1.19.0.post1";
in
{ lib
, pythonPackages
, buildPythonPackage
, tahoe-lafs-src
}:
buildPythonPackage rec {
  inherit pname version;
  src = tahoe-lafs-src;
  propagatedBuildInputs = with pythonPackages; [
    attrs
    autobahn
    cbor2
    click
    collections-extended
    cryptography
    distro
    eliot
    filelock
    foolscap
    future
    klein
    magic-wormhole
    netifaces
    psutil
    pyyaml
    pycddl
    pyrsistent
    pyutil
    six
    treq
    twisted
    werkzeug
    zfec
    zope_interface
  ] ++
  # Get the dependencies for the Twisted extras we depend on, too.
  twisted.passthru.optional-dependencies.tls ++
  twisted.passthru.optional-dependencies.conch;

  # The test suite lives elsewhere.
  doCheck = false;

  passthru = {
    extras = with pythonPackages; {
      tor = [
        txtorcon
      ];
      i2p = [
        txi2p
      ];
      unittest = [
        beautifulsoup4
        html5lib
        fixtures
        hypothesis
        mock
        prometheus-client
        testtools
      ];
      integrationtest = [
        pytest
        pytest-twisted
        paramiko
        pytest-timeout
      ];
    };
  };

  meta = with lib; {
    homepage = "https://tahoe-lafs.org/";
    description = "secure, decentralized, fault-tolerant file store";
    # Also TGPPL
    license = licenses.gpl2Plus;
  };
}
