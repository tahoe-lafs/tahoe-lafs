{ rustPlatform, buildPythonPackage, fetchPypi }:
buildPythonPackage rec {
  pname = "pycddl";
  version = "0.1.11";
  format = "pyproject";

  src = fetchPypi {
    inherit pname version;
    hash = "sha256:08zdikim3qswvc61w0xkwxaqqpbchq65sw5madkrf1y13jmgkifh";
  };

  patches = [ ./Cargo.lock.patch ];

  cargoHash = "sha256:0w9w6mgfyd2v7bn8hkrv1syznmkkqr60a4bgjhc49ckqxiqn8bww";
  cargoDeps = rustPlatform.fetchCargoTarball {
    inherit src;
    name = "${pname}-${version}";
    hash = cargoHash;
    patches = [ ./Cargo.lock.patch ];
  };

  nativeBuildInputs = with rustPlatform; [
    cargoSetupHook
    maturinBuildHook
  ];
}
