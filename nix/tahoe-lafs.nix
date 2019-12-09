{ fetchFromGitHub, nettools, python
, twisted, foolscap, nevow, zfec
, setuptools, setuptoolsTrial, pyasn1, zope_interface
, service-identity, pyyaml, magic-wormhole, treq, appdirs
, beautifulsoup4, eliot, autobahn, cryptography
}:
python.pkgs.buildPythonPackage rec {
  version = "1.14.0.dev";
  name = "tahoe-lafs-${version}";
  # src = fetchFromGitHub {
  #   owner = "LeastAuthority";
  #   repo = "tahoe-lafs";
  #   # HEAD of an integration branch for all of the storage plugin stuff.  Last
  #   # updated October 4 2019.
  #   rev = "8c1f536ba4fbc01f3bc5f08412edbefc56ff7037";
  #   sha256 = "17d7pkbsgss3rhqf7ac7ylzbddi555rnkzz48zjqwq1zx1z2jhy6";
  # };
  src = ~/Work/python/tahoe-lafs;

  postPatch = ''
    sed -i "src/allmydata/util/iputil.py" \
        -es"|_linux_path = '/sbin/ifconfig'|_linux_path = '${nettools}/bin/ifconfig'|g"

    # Chroots don't have /etc/hosts and /etc/resolv.conf, so work around
    # that.
    for i in $(find src/allmydata/test -type f)
    do
      sed -i "$i" -e"s/localhost/127.0.0.1/g"
    done
  '';


  propagatedBuildInputs = with python.pkgs; [
    twisted foolscap nevow zfec appdirs
    setuptoolsTrial pyasn1 zope_interface
    service-identity pyyaml magic-wormhole treq
    beautifulsoup4 eliot autobahn cryptography setuptools
  ];

  checkInputs = with python.pkgs; [
    hypothesis
    testtools
    fixtures
  ];

  checkPhase = ''
    ${python}/bin/python -m twisted.trial -j4 allmydata
  '';
}
