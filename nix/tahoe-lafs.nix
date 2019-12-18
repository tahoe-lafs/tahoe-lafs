{ fetchFromGitHub, lib
, nettools, python
, twisted, foolscap, nevow, zfec
, setuptools, setuptoolsTrial, pyasn1, zope_interface
, service-identity, pyyaml, magic-wormhole, treq, appdirs
, beautifulsoup4, eliot, autobahn, cryptography
, html5lib
}:
python.pkgs.buildPythonPackage rec {
  version = "1.14.0.dev";
  name = "tahoe-lafs-${version}";
  src = lib.cleanSource ../.;

  postPatch = ''
    # Chroots don't have /etc/hosts and /etc/resolv.conf, so work around
    # that.
    for i in $(find src/allmydata/test -type f)
    do
      sed -i "$i" -e"s/localhost/127.0.0.1/g"
    done

    # Some tests are flaky or fail to skip when dependencies are missing.
    # This list is over-zealous because it's more work to disable individual
    # tests with in a module.

    # test_system is a lot of integration-style tests that do a lot of real
    # networking between many processes.  They sometimes fail spuriously.
    rm src/allmydata/test/test_system.py

    # Many of these tests don't properly skip when i2p or tor dependencies are
    # not supplied (and we are not supplying them).
    rm src/allmydata/test/test_i2p_provider.py
    rm src/allmydata/test/test_connections.py
    rm src/allmydata/test/cli/test_create.py
    rm src/allmydata/test/test_client.py
    rm src/allmydata/test/test_runner.py

    # Some eliot code changes behavior based on whether stdout is a tty or not
    # and fails when it is not.
    rm src/allmydata/test/test_eliotutil.py
  '';


  propagatedBuildInputs = with python.pkgs; [
    twisted foolscap nevow zfec appdirs
    setuptoolsTrial pyasn1 zope_interface
    service-identity pyyaml magic-wormhole treq
    eliot autobahn cryptography setuptools
  ];

  checkInputs = with python.pkgs; [
    hypothesis
    testtools
    fixtures
    beautifulsoup4
    html5lib
    nettools
  ];

  checkPhase = ''
    ${python}/bin/python -m twisted.trial -j $NIX_BUILD_CORES allmydata
  '';
}
