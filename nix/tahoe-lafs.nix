{ fetchFromGitHub, lib
, git, python
, twisted, foolscap, zfec
, setuptools, setuptoolsTrial, pyasn1, zope_interface
, service-identity, pyyaml, magic-wormhole, treq, appdirs
, beautifulsoup4, eliot, autobahn, cryptography, netifaces
, html5lib, pyutil, distro, configparser, klein, treq
}:
python.pkgs.buildPythonPackage rec {
  # Most of the time this is not exactly the release version (eg 1.16.0).
  # Give it a `post` component to make it look newer than the release version
  # and we'll bump this up at the time of each release.
  #
  # It's difficult to read the version from Git the way the Python code does
  # for two reasons.  First, doing so involves populating the Nix expression
  # with values from the source.  Nix calls this "import from derivation" or
  # "IFD" (<https://nixos.wiki/wiki/Import_From_Derivation>).  This is
  # discouraged in most cases - including this one, I think.  Second, the
  # Python code reads the contents of `.git` to determine its version.  `.git`
  # is not a reproducable artifact (in the sense of "reproducable builds") so
  # it is excluded from the source tree by default.  When it is included, the
  # package tends to be frequently spuriously rebuilt.
  version = "1.16.0.post1";
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
        isVersion = basename == "_version.py";
        isBytecode = ext == "pyc" || ext == "pyo";
        isBackup = lib.hasSuffix "~" basename;
        isTemporary = lib.hasPrefix "#" basename && lib.hasSuffix "#" basename;
        isSymlink = type == "symlink";
        isGit = type == "directory" && basename == ".git";
      in
      # Exclude all these things
      ! (isTox
      || isTrialTemp
      || isVersion
      || isBytecode
      || isBackup
      || isTemporary
      || isSymlink
      || isGit
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

    # Generate _version.py ourselves since we can't rely on the Python code
    # extracting the information from the .git directory we excluded.
    cat > src/allmydata/_version.py <<EOF

# This _version.py is generated from metadata by nix/tahoe-lafs.nix.

__pkgname__ = "tahoe-lafs"
real_version = "${version}"
full_version = "${version}"
branch = "master"
verstr = "${version}"
__version__ = verstr
EOF
'';


  nativeBuildInputs = [
    git
  ];

  propagatedBuildInputs = with python.pkgs; [
    twisted foolscap zfec appdirs
    setuptoolsTrial pyasn1 zope_interface
    service-identity pyyaml magic-wormhole treq
    eliot autobahn cryptography netifaces setuptools
    future pyutil distro configparser collections-extended
  ];

  checkInputs = with python.pkgs; [
    hypothesis
    testtools
    fixtures
    beautifulsoup4
    html5lib
    tenacity
    prometheus_client
  ];

  checkPhase = ''
    if ! $out/bin/tahoe --version | grep --fixed-strings "${version}"; then
      echo "Package version:"
      $out/bin/tahoe --version
      echo "Did not contain expected:"
      echo "${version}"
      exit 1
    else
      echo "Version string contained expected value \"${version}.\""
    fi
    ${python}/bin/python -m twisted.trial -j $NIX_BUILD_CORES allmydata
  '';
}
