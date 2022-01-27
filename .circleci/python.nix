# Define a helper environment for incidental Python tasks required on CI.
let
  sources = import ../nix/sources.nix;
in
{ pkgs ? import sources."nixpkgs-21.11" { }
}:
pkgs.mkShell {
  buildInputs = [
    pkgs.python3
  ];
}
