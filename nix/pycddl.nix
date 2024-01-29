# package https://gitlab.com/tahoe-lafs/pycddl
#
# also in the process of being pushed upstream
# https://github.com/NixOS/nixpkgs/pull/221220
#
# we should switch to the upstream package when it is available from our
# minimum version of nixpkgs.
#
# if you need to update this package to a new pycddl release then
#
# 1. change value given to `buildPythonPackage` for `version` to match the new
#    release
#
# 2. change the value given to `fetchPypi` for `sha256` to `lib.fakeHash`
#
# 3. run `nix-build`
#
# 4. there will be an error about a hash mismatch.  change the value given to
#    `fetchPypi` for `sha256` to the "actual" hash value report.
#
# 5. change the value given to `cargoDeps` for `hash` to lib.fakeHash`.
#
# 6. run `nix-build`
#
# 7. there will be an error about a hash mismatch.  change the value given to
#    `cargoDeps` for `hash` to the "actual" hash value report.
#
# 8. run `nix-build`.  it should succeed.  if it does not, seek assistance.
#
{ lib, fetchPypi, python, buildPythonPackage, rustPlatform }:
buildPythonPackage rec {
  pname = "pycddl";
  version = "0.6.1";
  format = "pyproject";

  src = fetchPypi {
    inherit pname version;
    sha256 = "sha256-63fe8UJXEH6t4l7ujV8JDvlGb7q3kL6fHHATFdklzFc=";
  };

  # Without this, when building for PyPy, `maturin build` seems to fail to
  # find the interpreter at all and then fails early in the build process with
  # an error saying "unsupported Python interpreter".  We can easily point
  # directly at the relevant interpreter, so do that.
  maturinBuildFlags = [ "--interpreter" python.executable ];

  nativeBuildInputs = with rustPlatform; [
    maturinBuildHook
    cargoSetupHook
  ];

  cargoDeps = rustPlatform.fetchCargoTarball {
    inherit src;
    name = "${pname}-${version}";
    hash = "sha256-ssDEKRd3Y9/10oXBZHCxvlRkl9KMh3pGYbCkM4rXThQ=";
  };
}
