{ pkgs

# The name of the Python derivation in nixpkgs for which to build the package.
, pythonVersion

# The Tahoe-LAFS package itself, including its test requirements.
, tahoe-lafs

# The mach-nix builder to use to build the test environment.
, mkPython
}:
let
  # If we want to get tahoe-lafs into a Python environment with a bunch of
  # *other* Python modules and let them interact in the usual way then we have
  # to ask mach-nix for tahoe-lafs and those other Python modules in the same
  # way - i.e., using `requirements`.  The other tempting mechanism,
  # `packagesExtra`, inserts an extra layer of Python environment and prevents
  # normal interaction between Python modules (as well as usually producing
  # file collisions in the packages that are both runtime and test
  # dependencies).  To get the tahoe-lafs we just built into the environment,
  # put it into nixpkgs using an overlay and tell mach-nix to get tahoe-lafs
  # from nixpkgs.
  overridesPre = [(self: super: { inherit tahoe-lafs; })];
  providers = tahoe-lafs.meta.mach-nix.providers // { tahoe-lafs = "nixpkgs"; };

  # Make the Python environment in which we can run the tests.
  python-env = mkPython {
    # Get the packaging fixes we already know we need from putting together
    # the runtime package.
    inherit (tahoe-lafs.meta.mach-nix) _;
    # Share the runtime package's provider configuration - combined with our
    # own that causes the right tahoe-lafs to be picked up.
    inherit providers overridesPre;
    # Use the specified Python version - which must match the version of the
    # Tahoe-LAFS package given.
    python = pythonVersion;

    # Now get everything into the environment.
    requirements = ''
      # Here we pull in the Tahoe-LAFS package itself.
      tahoe-lafs

      # Unfortunately mach-nix misses all of the Python dependencies of the
      # tahoe-lafs satisfied from nixpkgs.  Drag them in here.  This gives a
      # bit of a pyrrhic flavor to the whole endeavor but maybe mach-nix will
      # fix this soon.
      #
      # https://github.com/DavHau/mach-nix/issues/123
      # https://github.com/DavHau/mach-nix/pull/386
      ${tahoe-lafs.requirements}

      # txi2p-tahoe is another dependency with an environment marker that
      # mach-nix doesn't automatically pick up.
      txi2p-tahoe
    '';
  };
in
# Make a derivation that runs the unit test suite.
pkgs.runCommand "tahoe-lafs-tests" { } ''
  export TAHOE_LAFS_HYPOTHESIS_PROFILE=ci
  ${python-env}/bin/python -m twisted.trial -j $NIX_BUILD_CORES allmydata

  # It's not cool to put the whole _trial_temp into $out because it has weird
  # files in it we don't want in the store.  Plus, even all of the less weird
  # files are mostly just trash that's not meaningful if the test suite passes
  # (which is the only way we get $out anyway).
  #
  # The build log itself is typically available from `nix-store --read-log` so
  # we don't need to record that either.
  echo "passed" >$out

''
