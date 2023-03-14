# Build the package with the test suite enabled.
args@{...}: (import ../. args).override {
  doCheck = true;
}
