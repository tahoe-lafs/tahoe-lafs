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
