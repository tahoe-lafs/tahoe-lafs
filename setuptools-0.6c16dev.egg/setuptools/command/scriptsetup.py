from distutils.errors import DistutilsSetupError
from setuptools import Command
import sys

class scriptsetup(Command):
    action = (sys.platform == "win32"
                   and "set up .pyscript association and PATHEXT variable to run scripts"
                   or  "this does nothing on non-Windows platforms")

    user_options = [
        ('allusers', 'a',
         'make changes for all users of this Windows installation (requires Administrator privileges)'),
    ]
    boolean_options = ['allusers']

    def initialize_options(self):
        self.allusers = False

    def finalize_options(self):
        pass

    def run(self):
        if sys.platform != "win32":
            print "\n'scriptsetup' isn't needed on non-Windows platforms."
        else:
            do_scriptsetup(self.allusers)


def do_scriptsetup(allusers=False):
    print "\nSetting up environment to run scripts for %s..." % (allusers and "all users" or "the current user")

    from _winreg import HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, HKEY_CLASSES_ROOT, \
        REG_SZ, REG_EXPAND_SZ, KEY_QUERY_VALUE, KEY_SET_VALUE, \
        OpenKey, CreateKey, QueryValueEx, SetValueEx, FlushKey, CloseKey

    USER_ENV = "Environment"
    try:
        user_env = OpenKey(HKEY_CURRENT_USER, USER_ENV, 0, KEY_QUERY_VALUE)
    except WindowsError, e:
        raise DistutilsSetupError("I could not read the user environment from the registry.\n%r" % (e,))

    SYSTEM_ENV = "SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment"
    try:
        system_env = OpenKey(HKEY_LOCAL_MACHINE, SYSTEM_ENV, 0, KEY_QUERY_VALUE)
    except WindowsError, e:
        raise DistutilsSetupError("I could not read the system environment from the registry.\n%r" % (e,))


    # HKEY_CLASSES_ROOT is a merged view that would only confuse us.
    # <http://technet.microsoft.com/en-us/library/cc739822(WS.10).aspx>

    USER_CLASSES = "SOFTWARE\\Classes"
    try:
        user_classes = OpenKey(HKEY_CURRENT_USER, USER_CLASSES, 0, KEY_QUERY_VALUE)
    except WindowsError, e:
        raise DistutilsSetupError("I could not read the user filetype associations from the registry.\n%r" % (e,))

    SYSTEM_CLASSES = "SOFTWARE\\Classes"
    try:
        system_classes = OpenKey(HKEY_LOCAL_MACHINE, SYSTEM_CLASSES, 0, KEY_QUERY_VALUE)
    except WindowsError, e:
        raise DistutilsSetupError("I could not read the system filetype associations from the registry.\n%r" % (e,))


    def query(key, subkey, what):
        try:
            (value, type) = QueryValueEx(key, subkey)
        except WindowsError, e:
            if e.winerror == 2:  # not found
                return None
            raise DistutilsSetupError("I could not read %s from the registry.\n%r" % (what, e))

        # It does not matter that we don't expand environment strings, in fact it's better not to.

        if type != REG_SZ and type != REG_EXPAND_SZ:
            raise DistutilsSetupError("I expected the registry entry for %s to have a string type (REG_SZ or REG_EXPAND_SZ), "
                                      "and was flummoxed by it having type code %r." % (what, type))
        return (value, type)


    def open_and_query(key, path, subkey, what):
        try:
            read_key = OpenKey(key, path, 0, KEY_QUERY_VALUE)
        except WindowsError, e:
            if e.winerror == 2:  # not found
                return None
            raise DistutilsSetupError("I could not read %s from the registry because I could not open "
                                      "the parent key.\n%r" % (what, e))

        try:
            return query(read_key, subkey, what)
        finally:
            CloseKey(read_key)


    def update(key_name_path, subkey, desired_value, desired_type, goal, what):
        (key, name, path) = key_name_path

        (old_value, old_type) = open_and_query(key, path, subkey, what) or (None, None)
        if (old_value, old_type) == (desired_value, desired_type):
            print "Already done: %s." % (goal,)
            return False

        try:
            update_key = OpenKey(key, path, 0, KEY_SET_VALUE|KEY_QUERY_VALUE)
        except WindowsError, e:
            if e.winerror != 2:
                raise DistutilsSetupError("I tried to %s, but was not successful because I could not open "
                                          "the registry key %s\\%s for writing.\n%r"
                                          % (goal, name, path, e))
            try:
                update_key = CreateKey(key, path)
            except WindowsError, e:
                raise DistutilsSetupError("I tried to %s, but was not successful because the registry key %s\\%s "
                                          "did not exist, and I was unable to create it.\n%r"
                                          % (goal, name, path, e))

        (new_value, new_type) = (None, None)
        try:
            SetValueEx(update_key, subkey, 0, desired_type, desired_value)
        except WindowsError, e:
            raise DistutilsSetupError("I tried to %s, but was not able to set the subkey %r under %s\\%s to be %r.\n%r"
                                      % (goal, subkey, name, path, desired_value))
        else:
            (new_value, new_type) = query(update_key, subkey, what) or (None, None)
        finally:
            FlushKey(update_key)
            CloseKey(update_key)

        if (new_value, new_type) != (desired_value, desired_type):
            raise DistutilsSetupError("I tried to %s by setting the subkey %r under %s\\%s to be %r, "
                                      "and the call to SetValueEx succeeded, but the value ended up as "
                                      "%r instead (it was previously %r). Maybe the update was unexpectedly virtualized?"
                                      % (goal, subkey, name, path, desired_value, new_value, old_value))

        print "Done: %s." % (goal,)
        return True


    # Maintenance hazard: 'add_to_environment' and 'associate' use very similar, but not identical logic.

    def add_to_environment(varname, addition, change_allusers):
        changed = False
        what = "the %s environment variable %s" % (change_allusers and "system" or "user", varname)
        goal = "add %s to %s" % (addition, what)

        system_valueandtype = query(system_env, varname, "the system environment variable %s" % (varname,))
        user_valueandtype   = query(user_env,   varname, "the user environment variable %s" % (varname,))

        if change_allusers:
            (value, type) = system_valueandtype or (u'', REG_SZ)
            key_name_path = (HKEY_LOCAL_MACHINE, "HKEY_LOCAL_MACHINE", SYSTEM_ENV)
        else:
            (value, type) = user_valueandtype or system_valueandtype or (u'', REG_SZ)
            key_name_path = (HKEY_CURRENT_USER, "HKEY_CURRENT_USER", USER_ENV)

        if addition.lower() in value.lower().split(u';'):
            print "Already done: %s." % (goal,)
        else:
            changed |= update(key_name_path, varname, value + u';' + addition, type, goal, what)

        if change_allusers:
            # Also change any overriding environment entry for the current user.
            (user_value, user_type) = user_valueandtype or (u'', REG_SZ)
            split_value = user_value.lower().split(u';')

            if not (addition.lower() in split_value or u'%'+varname.lower()+u'%' in split_value):
                now_what = "the overriding user environment variable %s" % (varname,)
                changed |= update((HKEY_CURRENT_USER, "HKEY_CURRENT_USER", USER_ENV),
                                  varname, user_value + u';' + addition, user_type,
                                  "add %s to %s" % (addition, now_what), now_what)

        return changed


    def associate(ext, target, change_allusers):
        changed = False
        what = "the %s association for %s" % (change_allusers and "system" or "user", ext)
        goal = "associate the filetype %s with %s for %s" % (ext, target, change_allusers and "all users" or "the current user")

        try:
            if change_allusers:
                target_key = OpenKey(HKEY_LOCAL_MACHINE, "%s\\%s" % (SYSTEM_CLASSES, target), 0, KEY_QUERY_VALUE)
            else:
                target_key = OpenKey(HKEY_CLASSES_ROOT, target, 0, KEY_QUERY_VALUE)
        except WindowsError, e:
            raise DistutilsSetupError("I was going to %s, but that won't work because the %s class does not exist in the registry, "
                                      "as far as I can tell.\n%r" % (goal, target, e))
        CloseKey(target_key)

        system_key_name_path = (HKEY_LOCAL_MACHINE, "HKEY_LOCAL_MACHINE", "%s\\%s" % (SYSTEM_CLASSES, ext))
        user_key_name_path   = (HKEY_CURRENT_USER,  "HKEY_CURRENT_USER",  "%s\\%s" % (USER_CLASSES,   ext))

        system_valueandtype = open_and_query(system_classes, ext, "", "the system association for %s" % (ext,))
        user_valueandtype   = open_and_query(user_classes,   ext, "", "the user association for %s" % (ext,))

        if change_allusers:
            (value, type) = system_valueandtype or (u'', REG_SZ)
            key_name_path = system_key_name_path
        else:
            (value, type) = user_valueandtype or system_valueandtype or (u'', REG_SZ)
            key_name_path = user_key_name_path

        if value == target:
            print "Already done: %s." % (goal,)
        else:
            changed |= update(key_name_path, "", unicode(target), REG_SZ, goal, what)

        if change_allusers:
            # Also change any overriding association for the current user.
            (user_value, user_type) = user_valueandtype or (u'', REG_SZ)

            if user_value != target:
                changed |= update(user_key_name_path, "", unicode(target), REG_SZ,
                                  "associate the filetype %s with %s for the current user " \
                                      "(because the system association is overridden)" % (ext, target),
                                  "the overriding user association for %s" % (ext,))

        return changed


    def broadcast_settingchange(change_allusers):
        print "Broadcasting that the environment has changed, please wait..."

        # <http://support.microsoft.com/kb/104011/en-us>
        # <http://msdn.microsoft.com/en-us/library/ms644952(VS.85).aspx>
        # LRESULT WINAPI SendMessageTimeoutA(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam,
        #                                    UINT fuFlags, UINT uTimeout, PDWORD_PTR lpdwResult);

        try:
            from ctypes import WINFUNCTYPE, POINTER, windll, addressof, c_char_p
            from ctypes.wintypes import LONG, HWND, UINT, WPARAM, LPARAM, DWORD

            SendMessageTimeout = WINFUNCTYPE(POINTER(LONG), HWND, UINT, WPARAM, LPARAM, UINT, UINT, POINTER(POINTER(DWORD))) \
                                     (("SendMessageTimeoutA", windll.user32))
            HWND_BROADCAST   = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            SendMessageTimeout(HWND_BROADCAST, WM_SETTINGCHANGE, change_allusers and 1 or 0,
                               addressof(c_char_p("Environment")), SMTO_ABORTIFHUNG, 5000, None);
        except Exception, e:
            print "Warning: %r" % (e,)


    changed_assoc = associate(".pyscript", "Python.File", allusers)

    changed_env = False
    try:
        changed_env |= add_to_environment("PATHEXT", ".pyscript", allusers)
        changed_env |= add_to_environment("PATHEXT", ".pyw",      allusers)
    finally:
        CloseKey(user_env)
        CloseKey(system_env)

    if changed_assoc or changed_env:
        broadcast_settingchange(allusers)

    if changed_env:
        print "\n" \
              "Changes have been made to the persistent environment, but not\n" \
              "in this Command Prompt. Running installed Python scripts will\n" \
              "only work from new Command Prompts opened from now on.\n"
