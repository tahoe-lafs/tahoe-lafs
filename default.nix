# This is the flake-compat glue code.  It loads the flake and gives us its
# outputs.  This gives us backwards compatibility with pre-flake consumers.
# All of the real action is in flake.nix.
(import
  (
    let lock = builtins.fromJSON (builtins.readFile ./flake.lock); in
    fetchTarball {
      url = "https://github.com/edolstra/flake-compat/archive/${lock.nodes.flake-compat.locked.rev}.tar.gz";
      sha256 = lock.nodes.flake-compat.locked.narHash;
    }
  )
  { src = ./.; }
).defaultNix.default
