# Build the package with the integration test suite enabled.
args@{ forceFoolscap, runSlow, ...}:
(import ./tests.nix (builtins.removeAttrs args [ "forceFoolscap" "runSlow" ])).override {
  checks = [ "integration" ];
  integrationFeatures = (
    (if forceFoolscap then [ "force-foolscap" ] else []) ++
    (if runSlow then [ "runslow" ] else [])
  );
}
