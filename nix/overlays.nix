self: super: {
  python27 = super.python27.override {
    packageOverrides = python-self: python-super: {
      eliot = python-self.callPackage ./eliot.nix { };
      nevow = python-super.callPackage ./nevow.nix { };
    };
  };
}
