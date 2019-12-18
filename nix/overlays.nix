self: super: {
  python27 = super.python27.override {
    packageOverrides = python-self: python-super: {
      # eliot is not part of nixpkgs at all at this time.
      eliot = python-self.callPackage ./eliot.nix { };
      # The packaged version of Nevow is very slightly out of date but also
      # conflicts with the packaged version of Twisted.  Supply our own
      # slightly newer version.
      nevow = python-super.callPackage ./nevow.nix { };
    };
  };
}
