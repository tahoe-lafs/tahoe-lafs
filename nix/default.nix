{ pkgs ? import <nixpkgs> { } }:
let pkgs' = pkgs.extend (import ./overlays.nix);
in pkgs'.python2.pkgs.callPackage ./tahoe-lafs.nix { }
