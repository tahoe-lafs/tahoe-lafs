{ txtorcon, fetchPypi }:
txtorcon.overrideAttrs (old: rec {
  pname = "txtorcon";
  version = "23.5.0";
  src = fetchPypi {
    inherit pname version;
    hash = "sha256-k/2Aqd1QX2mNCGT+k9uLapwRRLX+uRUwggtw7YmCZRw=";
  };
})
