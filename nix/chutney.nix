{ python, buildPythonPackage }:
buildPythonPackage rec {
  format = "other";

  pname = "chutney";
  version = "0.0";
  src = builtins.fetchGit {
    name = pname;
    url = "https://gitlab.torproject.org/tpo/core/chutney.git";
    rev = "c4f6789ad2558dcbfeb7d024c6481d8112bfb6c2";
    shallow = true;
  };

  installPhase = ''
    dst="$out/lib/${python.libPrefix}/site-packages/"
    mkdir -p "$dst"
    cp -a $src/lib/chutney "$dst"
    cp -a $src/networks "$dst"/../networks
    cp -a $src/torrc_templates "$dst"/../torrc_templates
  '';
}
