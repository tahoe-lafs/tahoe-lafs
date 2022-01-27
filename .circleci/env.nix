# Define a helper environment for incidental Python tasks required on CI.
let
  sources = import ../nix/sources.nix;
in
{ pkgsVersion
, pkgs ? import sources.${pkgsVersion} { }
}:
{
  ssh = pkgs.openssh;
  python = pkgs.python3.withPackages (ps: [
    ps.setuptools
  ]);
}
