self: super: {
  python27 = super.python27.override {
    packageOverrides = python-self: python-super: {
      # eliot is not part of nixpkgs at all at this time.
      eliot = python-self.pythonPackages.callPackage ./eliot.nix { };

      # NixOS autobahn package has trollius as a dependency, although
      # it is optional. Trollius is unmaintained and fails on CI.
      autobahn = python-super.pythonPackages.callPackage ./autobahn.nix { };

      # Porting to Python 3 is greatly aided by the future package.  A
      # slightly newer version than appears in nixos 19.09 is helpful.
      future = python-super.pythonPackages.callPackage ./future.nix { };

      # Need version of pyutil that supports Python 3. The version in 19.09
      # is too old.
      pyutil = python-super.pythonPackages.callPackage ./pyutil.nix { };

      # Need a newer version of Twisted, too.
      twisted = python-super.pythonPackages.callPackage ./twisted.nix { };

      # collections-extended is not part of nixpkgs at this time.
      collections-extended = python-super.pythonPackages.callPackage ./collections-extended.nix { };
    };
  };

  python39 = super.python39.override {
    packageOverrides = python-self: python-super: {
      # collections-extended is not part of nixpkgs at this time.
      collections-extended = python-super.pythonPackages.callPackage ./collections-extended.nix { };
    };
  };
}
