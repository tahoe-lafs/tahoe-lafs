{ pkgs ? import <nixpkgs> { } }:
let pkgs' = import pkgs.path { overlays = [ (import ./overlays.nix) ]; };
in pkgs'.python2.pkgs.callPackage ./tahoe-lafs.nix { }
