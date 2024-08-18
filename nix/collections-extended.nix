# Package a version that's compatible with Python 3.11.  This can go away once
# https://github.com/mlenzen/collections-extended/pull/199 is merged and
# included in a version of nixpkgs we depend on.
{ fetchFromGitHub, collections-extended }:
collections-extended.overrideAttrs (old: {
  src = fetchFromGitHub {
    owner = "mlenzen";
    repo = "collections-extended";
    rev = "8b93390636d58d28012b8e9d22334ee64ca37d73";
    hash = "sha256-e7RCpNsqyS1d3q0E+uaE4UOEQziueYsRkKEvy3gCHt0=";
  };
})
