# Override various Python packages to create a package set that works for
# Tahoe-LAFS on CPython and PyPy.
self: super:
let

  # Run a function on a derivation if and only if we're building for PyPy.
  onPyPy = f: drv: if super.isPyPy then f drv else drv;

  # Disable a Python package's test suite.
  dontCheck = drv: drv.overrideAttrs (old: { doInstallCheck = false; });

  # string -> any -> derivation -> derivation
  #
  # If the overrideable function for the given derivation accepts an argument
  # with the given name, override it with the given value.
  #
  # Since we try to work with multiple versions of nixpkgs, sometimes we need
  # to override a parameter that exists in one version but not others.  This
  # makes it a bit easier to do so.
  overrideIfPresent = name: value: drv:
    if (drv.override.__functionArgs ? ${name})
    then drv.override { "${name}" = value; }
    else drv;

  # Disable building a Python package's documentation.
  dontBuildDocs = drv: (
    overrideIfPresent "sphinxHook" null (
      overrideIfPresent "sphinx-rtd-theme" null
        drv
    )
  ).overrideAttrs ({ outputs, ... }: {
    outputs = builtins.filter (x: "doc" != x) outputs;
  });

in {
  tahoe-lafs = self.callPackage ./tahoe-lafs.nix {
    # Define the location of the Tahoe-LAFS source to be packaged (the same
    # directory as contains this file).  Clean up as many of the non-source
    # files (eg the `.git` directory, `~` backup files, nix's own `result`
    # symlink, etc) as possible to avoid needing to re-build when files that
    # make no difference to the package have changed.
    tahoe-lafs-src = self.lib.cleanSource ../.;
  };

  # collections-extended is currently broken for Python 3.11 in nixpkgs but
  # we know where a working version lives.
  collections-extended = self.callPackage ./collections-extended.nix {
    # Avoid infinite recursion.
    inherit (super) collections-extended;
  };

  # greenlet is incompatible with PyPy but PyPy has a builtin equivalent.
  # Fixed in nixpkgs in a5f8184fb816a4fd5ae87136838c9981e0d22c67.
  greenlet = onPyPy (drv: null) super.greenlet;

  # tornado and tk pull in a huge dependency trees for functionality we don't
  # care about, also tkinter doesn't work on PyPy.
  matplotlib = onPyPy (matplotlib: matplotlib.override {
    tornado = null;
    enableTk = false;
  }) super.matplotlib;

  tqdm = onPyPy (tqdm: tqdm.override {
    # ibid.
    tkinter = null;
    # pandas is only required by the part of the test suite covering
    # integration with pandas that we don't care about.  pandas is a huge
    # dependency.
    pandas = null;
  }) super.tqdm;

  # The treq test suite depends on httpbin.  httpbin pulls in babel (flask ->
  # jinja2 -> babel) and arrow (brotlipy -> construct -> arrow).  babel fails
  # its test suite and arrow segfaults.
  treq = onPyPy dontCheck super.treq;

  # the six test suite fails on PyPy because it depends on dbm which the
  # nixpkgs PyPy build appears to be missing.  Maybe fixed in nixpkgs in
  # a5f8184fb816a4fd5ae87136838c9981e0d22c67.
  six = onPyPy dontCheck super.six;

  # Likewise for beautifulsoup4.
  beautifulsoup4 = onPyPy dontBuildDocs super.beautifulsoup4;

  # The autobahn test suite pulls in a vast number of dependencies for
  # functionality we don't care about.  It might be nice to *selectively*
  # disable just some of it but this is easier.
  autobahn = dontCheck super.autobahn;

  # and python-dotenv tests pulls in a lot of dependencies, including jedi,
  # which does not work on PyPy.
  python-dotenv = onPyPy dontCheck super.python-dotenv;

  # By default, the sphinx docs are built, which pulls in a lot of
  # dependencies - including jedi, which does not work on PyPy.
  hypothesis = onPyPy dontBuildDocs super.hypothesis;

  # flaky's test suite depends on nose and nose appears to have Python 3
  # incompatibilities (it includes `print` statements, for example).
  flaky = onPyPy dontCheck super.flaky;

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

  # CircleCI build systems don't have enough memory to run this test suite.
  lz4 = onPyPy dontCheck super.lz4;
}
