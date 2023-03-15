# package https://github.com/tahoe-lafs/txi2p
#
# if you need to update this package to a new txi2p release then
#
# 1. change value given to `buildPythonPackage` for `version` to match the new
#    release
#
# 2. change the value given to `fetchPypi` for `sha256` to `lib.fakeHash`
#
# 3. run `nix-build`
#
# 4. there will be an error about a hash mismatch.  change the value given to
#    `fetchPypi` for `sha256` to the "actual" hash value report.
#
# 5. if there are new runtime dependencies then add them to the argument list
#    at the top.  if there are new test dependencies add them to the
#    `checkInputs` list.
#
# 6. run `nix-build`.  it should succeed.  if it does not, seek assistance.
#
{ fetchPypi
, buildPythonPackage
, parsley
, twisted
, unittestCheckHook
}:
buildPythonPackage rec {
  pname = "txi2p-tahoe";
  version = "0.3.7";

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-+Vs9zaFS+ACI14JNxEme93lnWmncdZyFAmnTH0yhOiY=";
  };

  propagatedBuildInputs = [ twisted parsley ];
  checkInputs = [ unittestCheckHook ];
  pythonImportsCheck = [ "parsley" "ometa"];
}
