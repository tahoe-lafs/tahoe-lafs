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
