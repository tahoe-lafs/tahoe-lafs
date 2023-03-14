# Build the package with the integration test suite enabled.
args@{...}:
(import ./tests.nix args).override {
  checks = [ "integration" ];
}
