# Override various Python packages to create a package set that works for
# Tahoe-LAFS on CPython and PyPy.
self: super:
let
  # string -> any -> derivation -> derivation
  #
  # If the overrideable function for the given derivation accepts an argument
  # with the given name, override it with the given value.
  overrideIfPresent = name: value: drv:
    if (drv.override.__functionArgs ? ${name})
    then drv.override { "${name}" = value; }
    else drv;

  # Run a function on a derivation if and only if we're building for PyPy.
  onPyPy = f: drv: if super.isPyPy then f drv else drv;

  # Disable a Python package's test suite.
  dontCheck = drv: drv.overrideAttrs (old: { doInstallCheck = false; });

  # Disable building a Python package's documentation.
  dontBuildDocs = alsoDisable: drv: (drv.override ({
    sphinxHook = null;
  } // alsoDisable)).overrideAttrs ({ outputs, ... }: {
    outputs = builtins.filter (x: "doc" != x) outputs;
  });

in {
  # The main show.
  tahoe-lafs = self.callPackage ./tahoe-lafs.nix {
    tahoe-lafs-src = ../.;
    extrasNames = [];
    doCheck = false;
  };

  # Some dependencies aren't packaged in nixpkgs so supply our own packages.
  pycddl = self.callPackage ./pycddl.nix { };
  txi2p = self.callPackage ./txi2p.nix { };

  # collections-extended is currently broken for Python 3.11 in nixpkgs but
  # we know where a working version lives.
  collections-extended = self.callPackage ./collections-extended.nix {
    inherit (super) collections-extended;
  };

  # We only use werkzeug for some routing stuff so we don't need its
  # watch-the-filesystem-and-reload thingy.  And watchdog fails to build on
  # PyPy and CPython 3.11.
  werkzeug = super.werkzeug.override (old: { watchdog = null; });

  # greenlet is incompatible with PyPy but PyPy has a builtin equivalent.
  # Fixed in nixpkgs in a5f8184fb816a4fd5ae87136838c9981e0d22c67.
  greenlet = onPyPy (drv: null) super.greenlet;

  # tornado and tk pull in a huge dependency trees for functionality we don't
  # care about, also tkinter doesn't work on PyPy.
  matplotlib = super.matplotlib.override { tornado = null; enableTk = false; };

  tqdm = (super.tqdm.override {
    # ibid.
    tkinter = null;
    # pandas is only required by the part of the test suite covering
    # integration with pandas that we don't care about.  pandas is a huge
    # dependency.
    pandas = null;
  }).overrideAttrs (old: {
    # test_eta fails for some reason, just skip the whole test suite.
    doInstallCheck = false;
  });

  # The test suite has a dependency on pytest-regressions which has a
  # dependency on pillow which fails to build on PyPy.
  markdown-it-py = onPyPy dontCheck super.markdown-it-py;

  # The treq test suite depends on httpbin.  httpbin pulls in babel (flask ->
  # jinja2 -> babel) and arrow (brotlipy -> construct -> arrow).  babel fails
  # its test suite and arrow segfaults.
  treq = onPyPy dontCheck super.treq;

  # the six test suite fails on PyPy because it depends on dbm which the
  # nixpkgs PyPy build appears to be missing.  Maybe fixed in nixpkgs in
  # a5f8184fb816a4fd5ae87136838c9981e0d22c67.
  six = onPyPy dontCheck super.six;

  # Building the docs requires sphinx which brings in a dependency on babel,
  # the test suite of which fails.
  pyopenssl = onPyPy (drv: overrideIfPresent "sphinx-rtd-theme" null (dontBuildDocs {} drv)) super.pyopenssl;

  # Likewise for beautifulsoup4.
  beautifulsoup4 = onPyPy (dontBuildDocs {}) super.beautifulsoup4;

  # The autobahn test suite pulls in a vast number of dependencies for
  # functionality we don't care about.  It might be nice to *selectively*
  # disable just some of it but this is easier.
  autobahn = onPyPy dontCheck super.autobahn;

  # and python-dotenv tests pulls in a lot of dependencies, including jedi,
  # which does not work on PyPy.
  python-dotenv = onPyPy dontCheck super.python-dotenv;

  # Upstream package unaccountably includes a sqlalchemy dependency ... but
  # the project has no such dependency.  Fixed in nixpkgs in
  # da10e809fff70fbe1d86303b133b779f09f56503.
  aiocontextvars = overrideIfPresent "sqlalchemy" null super.aiocontextvars;

  # By default, the sphinx docs are built, which pulls in a lot of
  # dependencies - including jedi, which does not work on PyPy.
  hypothesis =
    (let h = super.hypothesis;
     in
       if (h.override.__functionArgs.enableDocumentation or false)
       then h.override { enableDocumentation = false; }
       else h).overrideAttrs ({ nativeBuildInputs, ... }: {
         # The nixpkgs expression is missing the tzdata check input.
         nativeBuildInputs = nativeBuildInputs ++ [ super.tzdata ];
       });

  # flaky's test suite depends on nose and nose appears to have Python 3
  # incompatibilities (it includes `print` statements, for example).
  flaky = onPyPy dontCheck super.flaky;

  # Replace the deprecated way of running the test suite with the modern way.
  # This also drops a bunch of unnecessary build-time dependencies, some of
  # which are broken on PyPy.  Fixed in nixpkgs in
  # 5feb5054bb08ba779bd2560a44cf7d18ddf37fea.
  zfec = (
    overrideIfPresent "setuptoolsTrial" null super.zfec
  ).overrideAttrs (old: {
    checkPhase = "trial zfec";
  });

  # collections-extended is packaged with poetry-core.  poetry-core test suite
  # uses virtualenv and virtualenv test suite fails on PyPy.
  poetry-core = onPyPy dontCheck super.poetry-core;

  # The test suite fails with some rather irrelevant (to us) string comparison
  # failure on PyPy.  Probably a PyPy bug but doesn't seem like we should
  # care.
  rich = onPyPy dontCheck super.rich;

  # The pyutil test suite fails in some ... test ... for some deprecation
  # functionality we don't care about.
  pyutil = onPyPy dontCheck super.pyutil;

  # testCall1 fails fairly inscrutibly on PyPy.  Perhaps someone can fix that,
  # or we could at least just skip that one test.  Probably better to fix it
  # since we actually depend directly and significantly on Foolscap.
  foolscap = onPyPy dontCheck super.foolscap;

  # Fixed by nixpkgs PR https://github.com/NixOS/nixpkgs/pull/222246
  psutil = super.psutil.overrideAttrs ({ pytestFlagsArray, disabledTests, ...}: {
    # Upstream already disables some tests but there are even more that have
    # build impurities that come from build system hardware configuration.
    # Skip them too.
    pytestFlagsArray = [ "-v" ] ++ pytestFlagsArray;
    disabledTests = disabledTests ++ [ "sensors_temperatures" ];
  });

  # CircleCI build systems don't have enough memory to run this test suite.
  lz4 = dontCheck super.lz4;
}
