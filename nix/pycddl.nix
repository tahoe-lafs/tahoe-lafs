# package https://gitlab.com/tahoe-lafs/pycddl
#
# also in the process of being pushed upstream
# https://github.com/NixOS/nixpkgs/pull/221220
#
# we should switch to the upstream package when it is available from our
# minimum version of nixpkgs
{ lib, fetchPypi, buildPythonPackage, rustPlatform }:
buildPythonPackage rec {
  pname = "pycddl";
  version = "0.4.0";
  format = "pyproject";

  src = fetchPypi {
    inherit pname version;
    sha256 = "sha256-w0CGbPeiXyS74HqZXyiXhvaAMUaIj5onwjl9gWKAjqY=";
  };

  nativeBuildInputs = with rustPlatform; [
    maturinBuildHook
    cargoSetupHook
  ];

  cargoDeps = rustPlatform.fetchCargoTarball {
    inherit src;
    name = "${pname}-${version}";
    hash = "sha256-g96eeaqN9taPED4u+UKUcoitf5aTGFrW2/TOHoHEVHs=";
  };
}
