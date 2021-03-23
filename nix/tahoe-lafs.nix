{ fetchFromGitHub, lib
, git, python
, twisted, foolscap, zfec
, setuptools, setuptoolsTrial, pyasn1, zope_interface
, service-identity, pyyaml, magic-wormhole, treq, appdirs
, beautifulsoup4, eliot, autobahn, cryptography, netifaces
, html5lib, pyutil, distro, configparser
}:
python.pkgs.buildPythonPackage rec {
  version = "1.14.0.dev";
  name = "tahoe-lafs-${version}";
  src = lib.cleanSourceWith {
    src = ../.;
    filter = name: type:
      let
        basename = baseNameOf name;

        split = lib.splitString ".";
        join = builtins.concatStringsSep ".";
        ext = join (builtins.tail (split basename));

        # Build up a bunch of knowledge about what kind of file this is.
        isTox = type == "directory" && basename == ".tox";
        isTrialTemp = type == "directory" && basename == "_trial_temp";
        isVersion = basename == "version.py";
        isBytecode = ext == "pyc" || ext == "pyo";
        isBackup = lib.hasSuffix "~" basename;
        isTemporary = lib.hasPrefix "#" basename && lib.hasSuffix "#" basename;
        isSymlink = type == "symlink";
      in
      # Exclude all these things
      ! (isTrialTemp
      || isTox
      || isVersion
      || isBytecode
      || isBackup
      || isTemporary
      || isSymlink
      );
  };

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

    # Many of these tests don't properly skip when i2p or tor dependencies are
    # not supplied (and we are not supplying them).
    rm src/allmydata/test/test_i2p_provider.py
    rm src/allmydata/test/test_connections.py
    rm src/allmydata/test/cli/test_create.py

    # Since we're deleting files, this complains they're missing. For now Nix
    # is Python 2-only, anyway, so these tests don't add anything yet.
    rm src/allmydata/test/test_python3.py
  '';


  nativeBuildInputs = [
    git
  ];

  propagatedBuildInputs = with python.pkgs; [
    twisted foolscap zfec appdirs
    setuptoolsTrial pyasn1 zope_interface
    service-identity pyyaml magic-wormhole treq
    eliot autobahn cryptography netifaces setuptools
    future pyutil distro configparser
  ];

  checkInputs = with python.pkgs; [
    hypothesis
    testtools
    fixtures
    beautifulsoup4
    html5lib
    tenacity
  ];

  checkPhase = ''
    ${python}/bin/python -m twisted.trial -j $NIX_BUILD_CORES allmydata
  '';
}
