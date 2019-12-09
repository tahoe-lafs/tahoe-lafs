{ pkgs ? import <nixpkgs> { } }:
pkgs.python2.pkgs.callPackage ./tahoe-lafs.nix {
  eliot = (pkgs.python2.pkgs.callPackage ./eliot.nix { });
}
