
BACKEND_URL = 'https://www-test.allmydata.com/native_client2.php'
REGISTER_PAGE = 'https://www-test.allmydata.com/register'
TAHOESVC_NAME = 'Tahoe'
WINFUSESVC_NAME = 'Allmydata Tahoe SMB'

import os
import sys
import traceback
import urllib2
from urllib import urlencode
import webbrowser
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

            self.frame = ConfWizFrame(self)
            self.frame.CenterOnScreen()
            self.SetTopWindow(self.frame)
            self.SetExitOnFrameDelete(True)
            self.frame.Show(True)

            return True
        except:
            DisplayTraceback('config wizard init threw an exception')

class ConfWizFrame(wx.Frame):
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
        self.register_panel = RegisterPanel(background)
        sizer = wx.BoxSizer(wx.VERTICAL)
        background_sizer = wx.BoxSizer(wx.VERTICAL)
        background_sizer.Add(self.login_panel, 1, wx.ALIGN_CENTER_HORIZONTAL | wx.ALL, 26)
        background_sizer.Add(self.register_panel, 0, wx.ALIGN_CENTER_HORIZONTAL | wx.ALL, 26)
        background.SetSizer(background_sizer)
        sizer.Add(background, 0, wx.EXPAND | wx.ALL, 0)
        self.SetSizer(sizer)
        self.SetAutoLayout(True)
        self.Fit()
        self.Layout()

    def close(self, event):
        sys.exit()

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
        #self.sizer.Add(self.warning_label, 0, wx.EXPAND | wx.ALL, 2)
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
        self.warning_label.SetLabel('')

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

class RegisterPanel(wx.Panel):
    def __init__(self, parent):
        wx.Panel.__init__(self, parent, -1)
        self.parent = parent

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
        webbrowser.open(REGISTER_PAGE)

def main():
    app = ConfWizApp()
    app.MainLoop()


if __name__ == '__main__':
    main()
