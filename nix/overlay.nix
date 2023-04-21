let
  override = {
    packageOverrides = import ./python-overrides.nix;
  };

  overridePython = python: python.override (old: override);
in
final: prev:
{
  python38  = overridePython prev.python38;
  python39  = overridePython prev.python39;
  python310 = overridePython prev.python310;
  python311 = overridePython prev.python311;
  python312 = overridePython prev.python312;

  pypy38    = overridePython prev.pypy38;
  pypy39    = overridePython prev.pypy39;
  pypy310   = overridePython prev.pypy310;
  pypy311   = overridePython prev.pypy311;
  pypy312   = overridePython prev.pypy312;
}
