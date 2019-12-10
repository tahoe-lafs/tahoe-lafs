{ pkgs ? import <nixpkgs> { overlays = [ (import ./overlays.nix) ]; } }:
pkgs.python2.pkgs.callPackage ./tahoe-lafs.nix { }
