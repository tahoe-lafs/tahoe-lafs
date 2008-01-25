
BACKEND_URL = 'https://www-test.allmydata.com/native_client.php'
ACCOUNT_PAGE = 'https://www-test.allmydata.com/account'
TAHOESVC_NAME = 'Tahoe'
WINFUSESVC_NAME = 'Allmydata Tahoe SMB'

import os
import sys
#import time
import traceback
import urllib2
from urllib import urlencode
#import webbrowser
import wx

from allmydata.util.assertutil import precondition
from allmydata import uri

import amdicon


class AuthError(Exception):
    pass

def unicode_to_utf8(uobj):
    assert precondition(isinstance(uobj, unicode))
    return uobj.encode('utf-8')


def post(url, args):
    argstr = urlencode(args)
    conn = urllib2.urlopen(url, argstr)
    return conn.read()

def get_root_cap(url, user, passwd):
    args = {
        'action': 'authenticate',
        'email': unicode_to_utf8(user),
        'passwd': unicode_to_utf8(passwd),
        }
    root_cap = post(url, args)
    if root_cap == '0':
        raise AuthError()
    elif not uri.is_uri(root_cap):
        raise ValueError('%r is not a URI' % (root_cap,))
    else:
        return root_cap

def create_account(url, user, passwd, subscribe):
    args = {
        'action': 'create_account',
        'email': unicode_to_utf8(user),
        'passwd': unicode_to_utf8(passwd),
        'subscribe': subscribe and 'true' or 'false',
        }
    result_code = post(url, args)
    return result_code

def get_introducer_furl(url):
    return post(url, { 'action': 'getintroducerfurl' })

def write_config_file(filename, contents):
    if sys.platform == 'win32':
        from allmydata.windows import registry
        basedir = registry.get_base_dir_path()
    else:
        basedir = os.path.expanduser('~/.tahoe')
    path = os.path.join(basedir, filename)
    dirname = os.path.dirname(path)
    if not os.path.exists(dirname):
        os.makedirs(dirname)
    iff = file(path, 'wb')
    iff.write(contents)
    iff.close()


def DisplayTraceback(message):
    xc = traceback.format_exception(*sys.exc_info())
    wx.MessageBox(u"%s\n (%s)"%(message,''.join(xc)), 'Error')

class ConfWizApp(wx.App):
    def __init__(self):
        wx.App.__init__(self, 0)

    def OnInit(self):
        try:
            wx.InitAllImageHandlers()

            self.login_frame = LoginFrame(self)
            self.login_frame.CenterOnScreen()
            self.SetTopWindow(self.login_frame)
            #self.SetExitOnFrameDelete(True)
            self.login_frame.Show(True)

            return True
        except:
            DisplayTraceback('config wizard init threw an exception')

    def swap_to_register_frame(self):
        try:
            self.login_frame.Show(False)
            self.regiser_frame = RegisterFrame(self)
            self.regiser_frame.CenterOnScreen()
            self.SetTopWindow(self.regiser_frame)
            self.SetExitOnFrameDelete(True)
            self.regiser_frame.Show(True)
        except:
            DisplayTraceback('config wizard threw an exception')

class LoginFrame(wx.Frame):
    def __init__(self, app):
        title = 'Allmydata Tahoe Config Wizard'
        wx.Frame.__init__(self, None, -1, title)
        self.app = app
        self.SetSizeHints(100, 100, 600, 800)
        self.SetIcon(amdicon.getIcon())
        self.Bind(wx.EVT_CLOSE, self.close)

        background = wx.Panel(self, -1)
        background.parent = self
        self.login_panel = LoginPanel(background)
        self.reg_btn_panel = RegisterButtonPanel(background, app)
        sizer = wx.BoxSizer(wx.VERTICAL)
        background_sizer = wx.BoxSizer(wx.VERTICAL)
        background_sizer.Add(self.login_panel, 1, wx.ALIGN_CENTER_HORIZONTAL | wx.ALL, 26)
        background_sizer.Add(self.reg_btn_panel, 0, wx.ALIGN_CENTER_HORIZONTAL | wx.ALL, 26)
        background.SetSizer(background_sizer)
        sizer.Add(background, 0, wx.EXPAND | wx.ALL, 0)
        self.SetSizer(sizer)
        self.SetAutoLayout(True)
        self.Fit()
        self.Layout()

    def close(self, event):
        self.Show(False)
        self.app.ExitMainLoop()

class RegisterFrame(wx.Frame):
    def __init__(self, app):
        title = 'Allmydata Tahoe Config Wizard'
        wx.Frame.__init__(self, None, -1, title)
        self.app = app
        self.SetSizeHints(100, 100, 600, 800)
        self.SetIcon(amdicon.getIcon())
        self.Bind(wx.EVT_CLOSE, self.close)

        background = wx.Panel(self, -1)
        background.parent = self
        self.register_panel = RegisterPanel(background)
        sizer = wx.BoxSizer(wx.VERTICAL)
        background_sizer = wx.BoxSizer(wx.VERTICAL)
        background_sizer.Add(self.register_panel, 0, wx.ALIGN_CENTER_HORIZONTAL | wx.ALL, 26)
        background.SetSizer(background_sizer)
        sizer.Add(background, 0, wx.EXPAND | wx.ALL, 0)
        self.SetSizer(sizer)
        self.SetAutoLayout(True)
        self.Fit()
        self.Layout()

    def close(self, event):
        self.Show(False)
        self.app.ExitMainLoop()


class LoginPanel(wx.Panel):
    def __init__(self, parent):
        wx.Panel.__init__(self, parent, -1)
        self.parent = parent

        self.sizer = wx.BoxSizer(wx.VERTICAL)

        self.user_label = wx.StaticText(self, -1, 'Email')
        self.pass_label = wx.StaticText(self, -1, 'Password')
        self.user_field = wx.TextCtrl(self, -1, u'', size=(260,-1))
        self.pass_field = wx.TextCtrl(self, -1, u'', size=(260,-1), style=wx.TE_PASSWORD)
        self.login_button = wx.Button(self, -1, 'Sign In')
        self.warning_label = wx.StaticText(self, -1, '')
        self.warning_label.SetOwnForegroundColour(wx.RED)
        wx.EVT_CHAR(self.user_field, self.on_user_entry)
        wx.EVT_CHAR(self.pass_field, self.on_pass_entry)
        self.Bind(wx.EVT_BUTTON, self.on_login, self.login_button)
        login_sizer = wx.FlexGridSizer(3, 2, 5, 4)
        login_sizer.Add(self.user_label, 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        login_sizer.Add(self.user_field, 0, wx.EXPAND | wx.ALL, 2)
        login_sizer.Add(self.pass_label, 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        login_sizer.Add(self.pass_field, 0, wx.EXPAND | wx.ALL, 2)
        login_sizer.Add(wx.Size(2,2), 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        login_sizer.Add(self.login_button, 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        self.sizer.Add(login_sizer, 1, wx.EXPAND | wx.ALL, 2)
        self.sizer.Add(self.warning_label, 0, wx.CENTER | wx.ALL, 2)
        self.SetSizer(self.sizer)
        self.SetAutoLayout(True)

    def on_user_entry(self, event):
        if event.GetKeyCode() == wx.WXK_RETURN:
            self.pass_field.SetFocus()
        else:
            event.Skip()

    def on_pass_entry(self, event):
        if event.GetKeyCode() == wx.WXK_RETURN:
            self.on_login(event)
        else:
            event.Skip()

    def on_login(self, event):
        user = self.user_field.GetValue()
        passwd = self.pass_field.GetValue()
        self.warning_label.SetLabel('Connecting...')
        self.Layout()
        wx.Yield()

        if passwd == '':
            self.warning_label.SetLabel('You must enter a password')
            self.pass_field.SetFocus()
            self.Layout()
            return

        try:
            root_cap = get_root_cap(BACKEND_URL, user, passwd)
            write_config_file('private/root_dir.cap', root_cap+'\n')
        except AuthError:
            self.warning_label.SetLabel('Your email and/or password is incorrect')
            self.user_field.SetFocus()
            self.Layout()
            return

        # fetch the introducer furl
        ifurl = get_introducer_furl(BACKEND_URL)
        write_config_file('introducer.furl', ifurl+'\n')

        # start service etc.
        if sys.platform == 'win32':
            self.start_windows_service(TAHOESVC_NAME)
            self.start_windows_service(WINFUSESVC_NAME)

        # exit
        self.parent.parent.Close()

    def start_windows_service(self, svc_name):
        try:
            import win32service
            import win32serviceutil as wsu
            if wsu.QueryServiceStatus(svc_name)[1] != win32service.SERVICE_RUNNING:
                wsu.StartService(svc_name)
        except:
            DisplayTraceback('Failed to start windows service "%s"' % (svc_name,))

class RegisterButtonPanel(wx.Panel):
    def __init__(self, parent, app):
        wx.Panel.__init__(self, parent, -1)
        self.parent = parent
        self.app = app

        self.sizer = wx.BoxSizer(wx.VERTICAL)

        self.reg_label = wx.StaticText(self, -1, "Don't have an account?")
        self.reg_button = wx.Button(self, -1, 'Create Account')
        self.Bind(wx.EVT_BUTTON, self.on_reg_button, self.reg_button)
        reg_sizer = wx.FlexGridSizer(1, 2, 5, 4)
        reg_sizer.Add(self.reg_label, 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        reg_sizer.Add(self.reg_button, 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        self.sizer.Add(reg_sizer, 1, wx.EXPAND | wx.ALL, 2)
        self.SetSizer(self.sizer)
        self.SetAutoLayout(True)

    def on_reg_button(self, event):
        self.app.swap_to_register_frame()

class RegisterPanel(wx.Panel):
    def __init__(self, parent):
        wx.Panel.__init__(self, parent, -1)
        self.parent = parent

        self.sizer = wx.BoxSizer(wx.VERTICAL)

        self.user_label = wx.StaticText(self, -1, 'Email')
        self.pass_label = wx.StaticText(self, -1, 'Password')
        self.conf_label = wx.StaticText(self, -1, 'Confirm Password')
        self.user_field = wx.TextCtrl(self, -1, u'', size=(260,-1))
        self.pass_field = wx.TextCtrl(self, -1, u'', size=(260,-1), style=wx.TE_PASSWORD)
        self.conf_field = wx.TextCtrl(self, -1, u'', size=(260,-1), style=wx.TE_PASSWORD)
        self.create_account_button = wx.Button(self, -1, 'Create Account')
        self.subscribe_box = wx.CheckBox(self, -1, 'Sign up for our Newsletter')
        self.subscribe_box.SetValue(True)
        self.warning_label = wx.StaticText(self, -1, '')
        self.warning_label.SetOwnForegroundColour(wx.RED)
        wx.EVT_CHAR(self.user_field, self.on_user_entry)
        wx.EVT_CHAR(self.pass_field, self.on_pass_entry)
        wx.EVT_CHAR(self.conf_field, self.on_conf_entry)
        self.Bind(wx.EVT_BUTTON, self.on_create_account, self.create_account_button)
        login_sizer = wx.FlexGridSizer(4, 2, 5, 4)
        login_sizer.Add(self.user_label, 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        login_sizer.Add(self.user_field, 0, wx.EXPAND | wx.ALL, 2)
        login_sizer.Add(self.pass_label, 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        login_sizer.Add(self.pass_field, 0, wx.EXPAND | wx.ALL, 2)
        login_sizer.Add(self.conf_label, 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        login_sizer.Add(self.conf_field, 0, wx.EXPAND | wx.ALL, 2)
        login_sizer.Add(wx.Size(2,2), 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        login_sizer.Add(self.create_account_button, 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        self.sizer.Add(login_sizer, 1, wx.EXPAND | wx.ALL, 2)
        self.sizer.Add(self.warning_label, 0, wx.CENTER | wx.ALL, 2)
        self.sizer.Add(self.subscribe_box, 0, wx.CENTER | wx.ALL, 2)
        self.SetSizer(self.sizer)
        self.SetAutoLayout(True)

    def on_user_entry(self, event):
        if event.GetKeyCode() == wx.WXK_RETURN:
            self.pass_field.SetFocus()
        else:
            event.Skip()

    def on_pass_entry(self, event):
        if event.GetKeyCode() == wx.WXK_RETURN:
            self.conf_field.SetFocus()
        else:
            event.Skip()

    def on_conf_entry(self, event):
        if event.GetKeyCode() == wx.WXK_RETURN:
            self.on_create_account(event)
        else:
            event.Skip()

    def on_create_account(self, event):
        user = self.user_field.GetValue()
        passwd = self.pass_field.GetValue()
        pconf = self.conf_field.GetValue()
        subscribe = self.subscribe_box.IsChecked()
        self.warning_label.SetLabel('Connecting...')
        self.Layout()
        wx.Yield()

        if passwd == '':
            self.warning_label.SetLabel('You must enter a password')
            self.pass_field.SetFocus()
            self.Layout()
            return

        if passwd != pconf:
            self.warning_label.SetLabel("Passwords don't match")
            self.pass_field.SetValue('')
            self.conf_field.SetValue('')
            self.pass_field.SetFocus()
            self.Layout()
            return

        #print 'calling create_account', time.asctime()
        result_code = create_account(BACKEND_URL, user, passwd, subscribe)

        if result_code == 'account_exists':
            # try and log into it; if valid, use it anyway
            try:
                #print 'calling get_root_cap (ae)', time.asctime()
                root_cap = get_root_cap(BACKEND_URL, user, passwd)
                write_config_file('private/root_dir.cap', root_cap+'\n')
            except AuthError:
                self.warning_label.SetLabel('That email address is already registered')
                self.user_field.SetFocus()
                self.Layout()
                return
        elif result_code == 'error':
            self.warning_label.SetLabel('an error occurred')
            self.user_field.SetFocus()
            self.Layout()
            return
        elif result_code == 'ok':
            #print 'calling get_root_cap (ok)', time.asctime()
            root_cap = get_root_cap(BACKEND_URL, user, passwd)
            write_config_file('private/root_dir.cap', root_cap+'\n')
        else:
            self.warning_label.SetLabel('an unexpected error occurred ("%s")' % (result_code,))
            self.user_field.SetFocus()
            self.Layout()
            return

        # fetch the introducer furl
        #print 'calling get_introducer_furl', time.asctime()
        ifurl = get_introducer_furl(BACKEND_URL)
        write_config_file('introducer.furl', ifurl+'\n')

        # start service etc.
        if sys.platform == 'win32':
            self.start_windows_service(TAHOESVC_NAME)
            self.start_windows_service(WINFUSESVC_NAME)

        # exit
        self.parent.parent.Close()

    def start_windows_service(self, svc_name):
        try:
            import win32service
            import win32serviceutil as wsu
            if wsu.QueryServiceStatus(svc_name)[1] != win32service.SERVICE_RUNNING:
                wsu.StartService(svc_name)
        except:
            DisplayTraceback('Failed to start windows service "%s"' % (svc_name,))


def main():
    app = ConfWizApp()
    app.MainLoop()


if __name__ == '__main__':
    main()
