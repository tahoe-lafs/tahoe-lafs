# Build the package with the unit test suite enabled.
args@{...}:
(import ./tests.nix args).override {
  checks = [ "unit" ];
}
