import sys
import winreg

_AMD_KEY = r"Software\Allmydata"
_BDIR_KEY = 'Base Dir Path'

if sys.platform not in ('win32'):
    class WindowsError(Exception): # stupid voodoo to appease pyflakes
        pass
    raise ImportError("registry cannot be used on non-windows systems")

def get_registry_setting(key, name, _topkey=None):
    """
    This function iterates through _topkey (if not None),
    HKEY_CURRENT_USER, and HKEY_LOCAL_MACHINE before giving up.

    @note: Only supports string values.

    @param key: The key we are searching.
    @type key: String

    @param name: The name of the setting we are querying.
    @type name: String
    """
    topkeys = [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]

    if _topkey:
        topkeys.insert(0, _topkey)

    for topkey in topkeys:
        try:
            regkey = winreg.OpenKey(topkey, key)

            sublen, vallen, timestamp = winreg.QueryInfoKey(regkey)
            for validx in range(vallen):
                keyname, value, keytype = winreg.EnumValue(regkey, validx)
                if keyname == name and keytype == winreg.REG_SZ:
                    return value

        except WindowsError:
            continue
    # We didn't find the key:
    raise KeyError(key, name, "registry setting not found")

def set_registry_setting(key, name, data, reg_type=winreg.REG_SZ,
                         _topkey=winreg.HKEY_LOCAL_MACHINE, create_key_if_missing=True):
    """
    Sets a registry setting.

    defaults to string values (REG_SZ) - overridable with reg_type.
    """
    try:
        regkey = winreg.OpenKey(_topkey, key, 0, winreg.KEY_SET_VALUE)
    except WindowsError:
        if create_key_if_missing:
            regkey = winreg.CreateKey(_topkey, key)
        else:
            raise KeyError(key, "registry key not found")

    try:
        winreg.DeleteValue(regkey, name)
    except:
        pass

    winreg.SetValueEx(regkey, name, 0, reg_type, data)

def get_registry_value(keyname):
    """
    retrieves a registry key value from within the Software/Allmydata Inc key
    """
    try:
        return get_registry_setting(_AMD_KEY, keyname)
    except KeyError:
        return None

def get_base_dir_path():
    return get_registry_value(_BDIR_KEY)
