# This overlay adds Tahoe-LAFS and all of its properly-configured Python
# package dependencies to a Python package set.  Downstream consumers can
# apply it to their own nixpkgs derivation to produce a Tahoe-LAFS package.
final: prev: {
  # Add our overrides such that they will be applied to any Python derivation
  # in nixpkgs.
  pythonPackagesExtensions = prev.pythonPackagesExtensions ++ [
    (import ./python-overrides.nix)
  ];
}
