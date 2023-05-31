{ lib }:
let
  makePython = final: prev: py:
    let
      self = prev.${py}.override {
        inherit self;
        packageOverrides = lib.composeManyExtensions final.pythonPackageOverlays;
      };
    in {
      ${py} = self;
      "${py}Packages" = final.${py}.pkgs;
    };
in
# This overlay adds Tahoe-LAFS and all of its properly-configured Python
# package dependencies to a Python package set.  Downstream consumers
# can apply it to their own nixpkgs derivation to produce a Tahoe-LAFS
# package.
final: prev: ({
  pythonPackageOverlays = (prev.pythonPackageOverlays or []) ++ [
    (import ./python-overrides.nix)
  ];
}
// (makePython final prev "python3")

// (makePython final prev "python38")
// (makePython final prev "python39")
// (makePython final prev "python310")
// (makePython final prev "python311")

// (makePython final prev "pypy38")
// (makePython final prev "pypy39")
)
