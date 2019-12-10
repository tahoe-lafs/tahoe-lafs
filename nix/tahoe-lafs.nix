{ fetchFromGitHub, nettools, python
, twisted, foolscap, nevow, zfec
, setuptools, setuptoolsTrial, pyasn1, zope_interface
, service-identity, pyyaml, magic-wormhole, treq, appdirs
, beautifulsoup4, eliot, autobahn, cryptography
, html5lib
}:
python.pkgs.buildPythonPackage rec {
  version = "1.14.0.dev";
  name = "tahoe-lafs-${version}";
  src = ../.;

  postPatch = ''
    sed -i "src/allmydata/util/iputil.py" \
        -es"|_linux_path = '/sbin/ifconfig'|_linux_path = '${nettools}/bin/ifconfig'|g"

    # Chroots don't have /etc/hosts and /etc/resolv.conf, so work around
    # that.
    for i in $(find src/allmydata/test -type f)
    do
      sed -i "$i" -e"s/localhost/127.0.0.1/g"
    done

    # Some tests are flaky or fail to skip when dependencies are missing.
    rm src/allmydata/test/test_system.py
    rm src/allmydata/test/test_i2p_provider.py
    rm src/allmydata/test/test_connections.py
    rm src/allmydata/test/cli/test_create.py
    rm src/allmydata/test/test_eliotutil.py
    rm src/allmydata/test/test_iputil.py
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
  ];

  checkPhase = ''
    ${python}/bin/python -m twisted.trial -j4 allmydata
  '';
}
