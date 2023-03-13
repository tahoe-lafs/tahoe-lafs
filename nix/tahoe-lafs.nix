{ buildPythonPackage
, tahoe-lafs-src
, extrasNames

# control how the test suite is run
, doCheck ? false

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

# twisted extra dependencies - if there is overlap with our dependencies we
# have to skip them since we can't have a name in the argument set twice.
, appdirs
, bcrypt
, idna
, pyasn1
, pyopenssl
, service-identity

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
    # Get the dependencies for the Twisted extras we depend on, too.
    twisted.passthru.optional-dependencies.tls
    twisted.passthru.optional-dependencies.conch
    werkzeug
    zfec
    zope_interface
  ] ++ pickExtraDependencies pythonExtraDependencies extrasNames;

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
