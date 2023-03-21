{ lib
, isPyPy
, python
, pythonPackages
, buildPythonPackage
, tahoe-lafs-src
, extrasNames

# control which test suites run
# may contain:
#
#   "unit" - run the unit tests
#
#   "integration" - run the integration tests
, checks ? []

# for the integration tests, feature flags to control certain settings
# may contain:
#
#   "forceFoolscap" - Configure nodes to use Foolscap even if GBS is available
#
#   "runslow" - Run integration tests even if they are marked slow
, integrationFeatures ? [ ]
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

  unitTestDependencies = with pythonPackages; [
    beautifulsoup4
    fixtures
    hypothesis
    mock
    prometheus-client
    testtools
  ];

  integrationTestDependencies = with pythonPackages; [
    html5lib
    paramiko
    pytest
    pytest-timeout
    pytest-twisted
  ];

  doUnit = builtins.elem "unit" checks;
  doIntegration = builtins.elem "integration" checks;

  # "python" is on $PATH for the cpython case but not for the PyPy case.
  # python.executable is on $PATH for both cases.
  py = python.executable;
in
buildPythonPackage rec {
  inherit pname version;
  src = tahoe-lafs-src;

  # Supply all of the build and runtime dependencies.
  propagatedNativeBuildInputs = pythonPackageDependencies;

  # The source doesn't include version information - so dump some in
  # to it here.
  postPatch =
    let
      versionContent = builtins.toFile "_version.py" ''
        # This _version.py is generated by tahoe-lafs.nix.

        __pkgname__ = "tahoe-lafs"

        # TODO: We can have more metadata after we switch to flakes.
        # Then the `self` input will have a `sourceInfo` attribute telling us
        # things like git revision, a revision counter, etc.
        real_version = "${version}"
        full_version = real_version
        branch = "master"
        verstr = real_version
        __version__ = verstr
      '';
    in
      ''
        cp ${versionContent} src/allmydata/_version.py
      '';

  # If either kind of check is enabled, run checks.
  doCheck = doUnit || doIntegration;

  # Additionally, give the "check" environment all of the build and
  # runtime dependencies test-only dependencies (for whichever test
  # suites are enabled).
  checkInputs = (
    lib.optionals (doUnit || doIntegration) unitTestDependencies ++
    lib.optionals doIntegration integrationTestDependencies
  );

  # Our own command line tool, tahoe, will not be on PATH yet but the
  # test suite may try to use it - so put it there.  We can also do
  # other general test environment setup here.
  preCheck = ''
    PATH=$out/bin:$PATH
    type -p flogtool || (echo "flogtool missing" && exit 1)
    type -p tahoe || (echo "tahoe missing" && exit 1)
    export TAHOE_LAFS_HYPOTHESIS_PROFILE=ci
    echo "PATH: $PATH"
  '';

  # Define how the tests are run.  Include commands for whichever test
  # suites are enabled.  Also be sure to let check hooks run.
  checkPhase =
    let
      feature = name: lib.optionalString (builtins.elem name integrationFeatures);
      pytestFlags = "${feature "forceFoolscap" "--force-foolscap"} ${feature "runslow" "--runslow"}";
      # The test suite encounters hundreds of errors and then hangs, if run
      # with -jN on PyPy.
      jobs = if isPyPy then "" else "-j $NIX_BUILD_CORES";
    in
      ''
      runHook preCheck
      ${lib.optionalString doUnit "${py} -m twisted.trial ${jobs} allmydata"}
      ${lib.optionalString doIntegration "${py} -m pytest --timeout=1800 -s -v ${pytestFlags} integration"}
      runHook postCheck
    '';

  meta = with lib; {
    homepage = "https://tahoe-lafs.org/";
    description = "secure, decentralized, fault-tolerant file store";
    # Also TGPPL
    license = licenses.gpl2Plus;
  };
}
