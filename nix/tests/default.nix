# Build the package with the test suite enabled.
(import ../../. {}).override {
  doCheck = true;
}
