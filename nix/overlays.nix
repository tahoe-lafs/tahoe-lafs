self: super: {
  python27 = super.python27.override {
    packageOverrides = python-self: python-super: {
      # eliot is not part of nixpkgs at all at this time.
      eliot = python-self.callPackage ./eliot.nix { };
      # The packaged version of Nevow is very slightly out of date but also
      # conflicts with the packaged version of Twisted.  Supply our own
      # slightly newer version.
      nevow = python-super.callPackage ./nevow.nix { };
      # NixOS autobahn package has trollius as a dependency, although
      # it is optional. Trollius is no longer maintained and fails on
      # CI.
      autobahn = python-super.callPackage ./autobahn.nix { };
    };
  };
}
