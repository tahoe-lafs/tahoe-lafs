{ lib
, pythonPackages
, buildPythonPackage
, tahoe-lafs-src
, extrasNames

# control how the test suite is run
, doCheck
}:
let
  pname = "tahoe-lafs";
  version = "1.18.0.post1";

  pickExtraDependencies = deps: extras: builtins.foldl' (accum: extra: accum  ++ deps.${extra}) [] extras;

  pythonExtraDependencies = with pythonPackages; {
    tor = [ txtorcon ];
    i2p = [ txi2p ];
  };

  pythonPackageDependencies = with pythonPackages; [
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
    pycddl
    pyrsistent
    pyutil
    six
    treq
    twisted
    # Get the dependencies for the Twisted extras we depend on, too.
    twisted.passthru.optional-dependencies.tls
    twisted.passthru.optional-dependencies.conch
    werkzeug
    zfec
    zope_interface
  ] ++ pickExtraDependencies pythonExtraDependencies extrasNames;

  pythonCheckDependencies = with pythonPackages; [
    beautifulsoup4
    fixtures
    hypothesis
    mock
    paramiko
    prometheus-client
    pytest
    pytest-timeout
    pytest-twisted
    testtools
    towncrier
  ];
in
buildPythonPackage {
  inherit pname version;
  src = tahoe-lafs-src;
  propagatedBuildInputs = pythonPackageDependencies;

  inherit doCheck;
  checkInputs = pythonCheckDependencies;
  checkPhase = ''
    export TAHOE_LAFS_HYPOTHESIS_PROFILE=ci
    python -m twisted.trial -j $NIX_BUILD_CORES allmydata
  '';

  meta = with lib; {
    homepage = "https://tahoe-lafs.org/";
    description = "secure, decentralized, fault-tolerant file store";
    # Also TGPPL
    license = licenses.gpl2Plus;
  };
}
