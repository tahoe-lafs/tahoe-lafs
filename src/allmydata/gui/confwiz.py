
DEFAULT_SERVER_URL = 'https://beta.allmydata.com/'

BACKEND = 'native_client.php'
ACCOUNT_PAGE = 'account'
WELCOME_PAGE = 'welcome_install'
TAHOESVC_NAME = 'Tahoe'
WINFUSESVC_NAME = 'Allmydata SMB'

import os
import re
import socket
import sys
#import time
import traceback
import urllib2
from urllib import urlencode
import webbrowser
import wx

from allmydata.util.assertutil import precondition
from allmydata import uri
import allmydata

#import amdicon
import amdlogo

import foolscap
from twisted.python import usage

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

def record_install(url, user, passwd, nodeid, nickname):
    args = {
        'action': 'record_install',
        'email': unicode_to_utf8(user),
        'passwd': unicode_to_utf8(passwd),
        'nodeid': nodeid,
        'moniker': nickname,
        }
    result_code = post(url, args)
    return result_code

def record_uninstall(url, nodeid):
    args = {
        'action': 'record_uninstall',
        'nodeid': nodeid,
        }
    result_code = post(url, args)
    return result_code

def get_introducer_furl(url):
    return post(url, { 'action': 'getintroducerfurl' })

def get_config(url, user, passwd):
    args = {
        'action': 'get_config',
        'email': unicode_to_utf8(user),
        'passwd': unicode_to_utf8(passwd),
        }
    config = post(url, args)
    return config

def get_basedir():
    if sys.platform == 'win32':
        from allmydata.windows import registry
        return registry.get_base_dir_path()
    else:
        return os.path.expanduser('~/.tahoe')

def write_config_file(filename, contents):
    basedir = get_basedir()
    path = os.path.join(basedir, filename)
    dirname = os.path.dirname(path)
    if not os.path.exists(dirname):
        os.makedirs(dirname)
    iff = file(path, 'wb')
    iff.write(contents)
    iff.close()

def get_nodeid():
    CERTFILE = "node.pem"
    certfile = os.path.join(get_basedir(), "private", CERTFILE)
    tub = foolscap.Tub(certFile=certfile)
    return tub.getTubID()

def get_nickname():
    nick = None
    nnfile = os.path.join(get_basedir(), 'nickname')
    if os.path.exists(nnfile):
        try:
            fh = file(nnfile, 'rb')
            nick = fh.read().strip()
            fh.close()
        except:
            DisplayTraceback('Failed to read existing nickname file %s' % (nnfile,))
    if not nick:
        nick = socket.gethostname()
    return nick

def maybe_write_file(filename, contents):
    fname = os.path.join(get_basedir(), filename)
    try:
        if not os.path.exists(fname):
            fh = file(fname, 'wb')
            fh.write(contents)
            fh.write('\n')
            fh.close()
    except:
        DisplayTraceback('Failed to write file %s' % (fname,))

def configure(backend, user, passwd):
    _config_re = re.compile('^([^:]*): (.*)$')
    config = get_config(backend, user, passwd)
    config_dict = {}
    for line in config.split('\n'):
        if line:
            m = _config_re.match(line)
            if m:
                fname, contents = m.groups()
                config_dict[fname] = contents
    for fname, contents in config_dict.items():
        write_config_file(fname, contents+'\n')

def start_windows_service(svc_name):
    try:
        import win32service
        import win32serviceutil as wsu
        if wsu.QueryServiceStatus(svc_name)[1] != win32service.SERVICE_RUNNING:
            wsu.StartService(svc_name)
    except:
        DisplayTraceback('Failed to start windows service "%s"' % (svc_name,))

def maybe_start_services():
    if sys.platform == 'win32':
        start_windows_service(TAHOESVC_NAME)
        start_windows_service(WINFUSESVC_NAME)

def DisplayTraceback(message):
    xc = traceback.format_exception(*sys.exc_info())
    wx.MessageBox(u"%s\n (%s)"%(message,''.join(xc)), 'Error')

class ConfWizApp(wx.App):
    def __init__(self, server, open_welcome_page=False):
        self.server = server
        self.show_welcome = open_welcome_page
        wx.App.__init__(self, 0)

    def get_backend(self):
        return self.server + BACKEND

    def open_welcome_page(self):
        if self.show_welcome:
            args = {'v':    str(allmydata.__version__),
                    'plat': sys.platform,
                    }
            webbrowser.open(self.server + WELCOME_PAGE + '?' + urlencode(args))

    def OnInit(self):
        try:
            wx.InitAllImageHandlers()

            self.login_frame = WizardFrame(self, LoginPanel)
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
            self.regiser_frame = WizardFrame(self, RegisterPanel)
            self.regiser_frame.CenterOnScreen()
            self.SetTopWindow(self.regiser_frame)
            self.SetExitOnFrameDelete(True)
            self.regiser_frame.Show(True)
        except:
            DisplayTraceback('config wizard threw an exception')

class WizardFrame(wx.Frame):
    def __init__(self, app, panel_class):
        #title = 'Allmydata Config Wizard'
        title = 'Setup - Allmydata 3.0'
        wx.Frame.__init__(self, None, -1, title)
        self.app = app
        self.SetIcon(amdlogo.getIcon())
        self.Bind(wx.EVT_CLOSE, self.close)

        self.SetSizeHints(500, 360, 600, 800)

        banner = wx.Panel(self, -1)
        banner.SetSize((496,58))
        banner.SetBackgroundColour(wx.WHITE)

        banner_title = wx.StaticText(banner, -1, panel_class.title)
        banner_desc = wx.StaticText(banner, -1, "        " + panel_class.description)
        font = banner_title.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        banner_title.SetFont(font)
        banner_icon = wx.StaticBitmap(banner, -1, amdlogo.getBitmap())
        banner_label_sizer = wx.BoxSizer(wx.VERTICAL)
        banner_label_sizer.Add(banner_title, 0, wx.EXPAND | wx.ALL, 2)
        banner_label_sizer.Add(banner_desc, 0, wx.EXPAND | wx.ALL, 2)

        banner_sizer = wx.BoxSizer(wx.HORIZONTAL)
        banner_sizer.Add(banner_label_sizer, 1, wx.EXPAND | wx.ALL, 12)
        banner_sizer.Add(banner_icon, 0, wx.ALL, 12)
        banner.SetSizer(banner_sizer)
        banner.SetAutoLayout(True)

        background = wx.Panel(self, -1, style=wx.SIMPLE_BORDER)
        background.parent = self

        button_panel = wx.Panel(self, -1)
        button_panel.SetSize((496, 64))

        self.panel = panel_class(background, button_panel, app)
        sizer = wx.BoxSizer(wx.VERTICAL)
        background_sizer = wx.BoxSizer(wx.VERTICAL)
        background_sizer.Add(wx.Size(2,2), 0, wx.EXPAND | wx.ALL, self.panel.padding)
        background_sizer.Add(self.panel, 1, wx.ALIGN_CENTER_HORIZONTAL | wx.ALL, 26)
        background_sizer.Add(wx.Size(2,2), 0, wx.EXPAND | wx.ALL, self.panel.padding)
        background.SetSizer(background_sizer)


        sizer.Add(banner, 0, wx.EXPAND | wx.HORIZONTAL, 0)
        sizer.Add(background, 0, wx.EXPAND | wx.ALL, 0)
        sizer.Add(button_panel, 0, wx.EXPAND | wx.HORIZONTAL, 0)
        self.SetSizer(sizer)
        self.SetAutoLayout(True)
        self.Fit()
        self.Layout()

    def close(self, event):
        self.Show(False)
        self.app.ExitMainLoop()


class LoginPanel(wx.Panel):
    padding = 26
    title = 'Sign in'
    description = 'Sign in to your existing account'

    def __init__(self, parent, button_panel, app):
        wx.Panel.__init__(self, parent, -1)
        self.parent = parent
        self.app = app

        self.sizer = wx.BoxSizer(wx.VERTICAL)

        self.user_label = wx.StaticText(self, -1, 'Email')
        self.pass_label = wx.StaticText(self, -1, 'Password')
        self.user_field = wx.TextCtrl(self, -1, u'', size=(260,-1))
        self.pass_field = wx.TextCtrl(self, -1, u'', size=(260,-1), style=wx.TE_PASSWORD)
        self.warning_label = wx.StaticText(self, -1, '')
        self.warning_label.SetOwnForegroundColour(wx.RED)
        wx.EVT_CHAR(self.user_field, self.on_user_entry)
        wx.EVT_CHAR(self.pass_field, self.on_pass_entry)
        login_sizer = wx.FlexGridSizer(2, 2, 5, 4)
        login_sizer.Add(self.user_label, 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        login_sizer.Add(self.user_field, 0, wx.EXPAND | wx.ALL, 2)
        login_sizer.Add(self.pass_label, 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        login_sizer.Add(self.pass_field, 0, wx.EXPAND | wx.ALL, 2)
        self.sizer.Add(login_sizer, 1, wx.EXPAND | wx.ALL, 2)
        self.sizer.Add(self.warning_label, 0, wx.CENTER | wx.ALL, 2)
        self.SetSizer(self.sizer)
        self.SetAutoLayout(True)

        self.reg_label = wx.StaticText(button_panel, -1, "Don't have an account?")
        self.reg_button = wx.Button(button_panel, -1, 'Create Account')
        self.login_button = wx.Button(button_panel, -1, 'Sign In')
        button_panel.Bind(wx.EVT_BUTTON, self.on_reg_button, self.reg_button)
        button_panel.Bind(wx.EVT_BUTTON, self.on_login, self.login_button)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(wx.Size(2,2), 1, wx.EXPAND | wx.ALL, 12)
        btn_sizer.Add(self.reg_label, 0, wx.ALIGN_RIGHT | wx.ALL, 12)
        btn_sizer.Add(self.reg_button, 0, wx.ALIGN_RIGHT | wx.ALL, 12)
        btn_sizer.Add(self.login_button, 0, wx.ALIGN_RIGHT | wx.ALL, 12)
        button_panel.SetSizer(btn_sizer)
        self.button_panel = button_panel

    def on_reg_button(self, event):
        self.app.swap_to_register_frame()

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

        backend = self.app.get_backend()

        if passwd == '':
            self.warning_label.SetLabel('You must enter a password')
            self.pass_field.SetFocus()
            self.Layout()
            return

        try:
            root_cap = get_root_cap(backend, user, passwd)
            write_config_file('private/root_dir.cap', root_cap+'\n')
        except AuthError:
            self.warning_label.SetLabel('Your email and/or password is incorrect')
            self.user_field.SetFocus()
            self.Layout()
            return

        nodeid = get_nodeid()
        nickname = get_nickname()
        ret = record_install(backend, user, passwd, nodeid, nickname)
        if ret != 'ok':
            wx.MessageBox('Error "%s" recording this system (%s)' % (ret, nodeid), 'Error')

        configure(backend, user, passwd)
        maybe_start_services()
        maybe_write_file('nickname', nickname)
        maybe_write_file('accountname', user)

        self.app.open_welcome_page()

        # exit
        self.parent.parent.Close()

class RegisterPanel(wx.Panel):
    padding = 7
    title = 'Create account'
    description = 'Create a new account on Allmydata'

    def __init__(self, parent, button_panel, app):
        wx.Panel.__init__(self, parent, -1)
        self.parent = parent
        self.app = app

        self.sizer = wx.BoxSizer(wx.VERTICAL)

        self.user_label = wx.StaticText(self, -1, 'Email')
        self.pass_label = wx.StaticText(self, -1, 'Password')
        self.conf_label = wx.StaticText(self, -1, 'Confirm Password')
        self.user_field = wx.TextCtrl(self, -1, u'', size=(260,-1))
        self.pass_field = wx.TextCtrl(self, -1, u'', size=(260,-1), style=wx.TE_PASSWORD)
        self.conf_field = wx.TextCtrl(self, -1, u'', size=(260,-1), style=wx.TE_PASSWORD)
        self.subscribe_box = wx.CheckBox(self, -1, 'Sign up for our Newsletter')
        self.subscribe_box.SetValue(True)
        self.warning_label = wx.StaticText(self, -1, '')
        self.warning_label.SetOwnForegroundColour(wx.RED)
        wx.EVT_CHAR(self.user_field, self.on_user_entry)
        wx.EVT_CHAR(self.pass_field, self.on_pass_entry)
        wx.EVT_CHAR(self.conf_field, self.on_conf_entry)
        login_sizer = wx.FlexGridSizer(3, 2, 5, 4)
        login_sizer.Add(self.user_label, 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        login_sizer.Add(self.user_field, 0, wx.EXPAND | wx.ALL, 2)
        login_sizer.Add(self.pass_label, 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        login_sizer.Add(self.pass_field, 0, wx.EXPAND | wx.ALL, 2)
        login_sizer.Add(self.conf_label, 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        login_sizer.Add(self.conf_field, 0, wx.EXPAND | wx.ALL, 2)
        login_sizer.Add(wx.Size(2,2), 0, wx.ALIGN_RIGHT | wx.ALL, 2)
        self.sizer.Add(login_sizer, 0, wx.EXPAND | wx.ALL, 2)
        self.sizer.Add(self.warning_label, 0, wx.CENTER | wx.ALL, 2)
        self.sizer.Add(wx.Size(2,2), 0, wx.EXPAND | wx.ALL, 4)
        self.sizer.Add(self.subscribe_box, 0, wx.CENTER | wx.ALL, 2)
        self.SetSizer(self.sizer)
        self.SetAutoLayout(True)

        self.reg_button = wx.Button(button_panel, -1, 'Create Account')
        button_panel.Bind(wx.EVT_BUTTON, self.on_create_account, self.reg_button)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(wx.Size(2,2), 1, wx.EXPAND | wx.ALL, 12)
        btn_sizer.Add(self.reg_button, 0, wx.ALIGN_RIGHT | wx.ALL, 12)
        button_panel.SetSizer(btn_sizer)
        self.button_panel = button_panel

        self.Fit()

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

        backend = self.app.get_backend()

        #print 'calling create_account', time.asctime()
        result_code = create_account(backend, user, passwd, subscribe)

        if result_code == 'account_exists':
            # try and log into it; if valid, use it anyway
            try:
                #print 'calling get_root_cap (ae)', time.asctime()
                root_cap = get_root_cap(backend, user, passwd)
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
            root_cap = get_root_cap(backend, user, passwd)
            write_config_file('private/root_dir.cap', root_cap+'\n')
        else:
            self.warning_label.SetLabel('an unexpected error occurred ("%s")' % (result_code,))
            self.user_field.SetFocus()
            self.Layout()
            return

        nodeid = get_nodeid()
        nickname = get_nickname()
        ret = record_install(backend, user, passwd, nodeid, nickname)
        if ret != 'ok':
            wx.MessageBox('Error "%s" recording this system (%s)' % (ret, nodeid), 'Error')

        configure(backend, user, passwd)
        maybe_start_services()
        maybe_write_file('nickname', nickname)
        maybe_write_file('accountname', user)

        self.app.open_welcome_page()

        # exit
        self.parent.parent.Close()

def do_uninstall(server_url):
    nodeid = get_nodeid()
    ret = record_uninstall(server_url + BACKEND, nodeid)
    print ret
    if ret != 'ok':
        print 'Error "%s" recording uninstall of this system (%s)' % (ret, nodeid)

class Options(usage.Options):
    synopsis = "Usage:  confwiz [options]"

    optFlags = [
        ['uninstall', 'u', 'record uninstall'],
        ]
    optParameters = [
        ['server', 's', DEFAULT_SERVER_URL, 'url of server to contact'],
        ]

def main(argv):
    config = Options()
    try:
        config.parseOptions(argv[1:])
    except usage.error, e:
        print config
        print "%s:  %s" % (sys.argv[0], e)
        sys.exit(-1)

    server = config['server']
    if not server.endswith('/'):
        server += '/'

    if config['uninstall']:
        do_uninstall(server)
    else:
        app = ConfWizApp(server)
        app.MainLoop()


if __name__ == '__main__':
    main(sys.argv)
