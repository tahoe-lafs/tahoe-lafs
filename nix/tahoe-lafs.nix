{ buildPythonPackage
, tahoe-lafs-src
, extras

# always dependencies
, attrs
, autobahn
, cbor2
, click
, collections-extended
, cryptography
, distro
, eliot
, filelock
, foolscap
, future
, klein
, magic-wormhole
, netifaces
, psutil
, pycddl
, pyrsistent
, pyutil
, six
, treq
, twisted
, werkzeug
, zfec
, zope_interface

# tor extra dependencies
, txtorcon

# i2p extra dependencies
, txi2p

# test dependencies
, beautifulsoup4
, fixtures
, hypothesis
, mock
, paramiko
, prometheus-client
, pytest
, pytest-timeout
, pytest-twisted
, tenacity
, testtools
, towncrier
}:
let
  pname = "tahoe-lafs";
  version = "1.18.0.post1";

  pickExtraDependencies = deps: extras: builtins.foldl' (accum: extra: accum  ++ deps.${extra}) [] extras;

  pythonExtraDependencies = {
    tor = [ txtorcon ];
    i2p = [ txi2p ];
  };

  pythonPackageDependencies = [
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
    (twisted.passthru.optional-dependencies.tls)
    (twisted.passthru.optional-dependencies.conch)
    werkzeug
    zfec
    zope_interface
  ] ++ pickExtraDependencies pythonExtraDependencies extras;

  pythonCheckDependencies = [
    beautifulsoup4
    fixtures
    hypothesis
    mock
    paramiko
    prometheus-client
    pytest
    pytest-timeout
    pytest-twisted
    tenacity
    testtools
    towncrier
  ];
in
buildPythonPackage {
  inherit pname version;
  src = tahoe-lafs-src;
  buildInputs = pythonPackageDependencies;
  checkInputs = pythonCheckDependencies;
  checkPhase = "TAHOE_LAFS_HYPOTHESIS_PROFILE=ci python -m twisted.trial -j $NIX_BUILD_CORES allmydata";
}
