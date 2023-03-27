# Build the package with the whole test suite enabled.
args@{ checks ? [ "unit" "integration" ], ...}:
(import ../. (builtins.removeAttrs args [ "checks" ])).override {
  inherit checks;
}
