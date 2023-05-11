{ klein, fetchPypi }:
klein.overrideAttrs (old: rec {
  pname = "klein";
  version = "23.5.0";
  src = fetchPypi {
    inherit pname version;
    sha256 = "sha256-kGkSt6tBDZp/NRICg5w81zoqwHe9AHHIYcMfDu92Aoc=";
  };
})
