
import operator
import os
import stat
import subprocess
import sys
import thread
import threading
import traceback
import urllib
import webbrowser

import wx
from twisted.internet import reactor
from twisted.python import log, logfile

import allmydata
from allmydata import client
from allmydata.gui.confwiz import ConfWizApp, ACCOUNT_PAGE, DEFAULT_SERVER_URL
from allmydata.uri import NewDirectoryURI
import amdicon

DEFAULT_FUSE_TIMEOUT = 300

TRY_TO_INSTALL_TAHOE_SCRIPT = True
TAHOE_SCRIPT = '''#!/bin/bash
if [ "x${*}x" == "xx" ]
then
    %(exe)s --help
else
    %(exe)s "${@}"
fi
'''

def run_macapp():
    basedir = os.path.expanduser('~/.tahoe')
    if not os.path.isdir(basedir):
        app_supp = os.path.expanduser('~/Library/Application Support/Allmydata Tahoe/')
        if not os.path.isdir(app_supp):
            os.makedirs(app_supp)
        os.symlink(app_supp, basedir)

    app = App(basedir)
    return app.run()

class MacGuiClient(client.Client):
    def __init__(self, basedir, app):
        self.app = app
        client.Client.__init__(self, basedir)

    def _service_startup_failed(self, failure):
        wx.CallAfter(self.wx_abort, failure)
        log.msg('node service startup failed')
        log.err(failure)

    def wx_abort(self, failure):
        wx.MessageBox(failure.getTraceback(), 'Fatal Error in Node startup')
        self.app.guiapp.ExitMainLoop()

class App(object):
    def __init__(self, basedir):
        self.basedir = basedir

    def files_exist(self, file_list):
        extant_conf = [ os.path.exists(os.path.join(self.basedir, f)) for f in file_list ]
        return reduce(operator.__and__, extant_conf)

    def is_config_incomplete(self):
        necessary_conf_files = ['introducer.furl', 'private/root_dir.cap']
        need_config = not self.files_exist(necessary_conf_files)
        if need_config:
            print 'some config is missing from basedir (%s): %s' % (self.basedir, necessary_conf_files)
        return need_config

    def run(self):
        # handle initial config
        if not os.path.exists(os.path.join(self.basedir, 'webport')):
            f = file(os.path.join(self.basedir, 'webport'), 'wb')
            f.write('8123')
            f.close()

        if self.is_config_incomplete():
            app = ConfWizApp(DEFAULT_SERVER_URL, open_welcome_page=True)
            app.MainLoop()

        if self.is_config_incomplete():
            print 'config still incomplete; confwiz cancelled, exiting'
            return 1

        # set up twisted logging. this will become part of the node rsn.
        logdir = os.path.join(self.basedir, 'logs')
        if not os.path.exists(logdir):
            os.makedirs(logdir)
        lf = logfile.LogFile('tahoesvc.log', logdir)
        log.startLogging(lf)

        if TRY_TO_INSTALL_TAHOE_SCRIPT:
            self.maybe_install_tahoe_script()

        # actually start up the node and the ui
        os.chdir(self.basedir)

        self.start_reactor()

        try:
            self.guiapp = MacGuiApp(app=self)
            self.guiapp.MainLoop()
            log.msg('gui mainloop exited')
        except:
            log.err()

        self.stop_reactor()

        return 0

    def start_reactor(self):
        self.reactor_shutdown = threading.Event()
        thread.start_new_thread(self.launch_reactor, ())

    def launch_reactor(self):
        # run the node itself
        #c = client.Client(self.basedir)
        c = MacGuiClient(self.basedir, self)
        reactor.callLater(0, c.startService) # after reactor startup
        reactor.run(installSignalHandlers=False)
        self.reactor_shutdown.set()

    def stop_reactor(self):
        # trigger reactor shutdown, and block waiting on it
        reactor.callFromThread(reactor.stop)
        log.msg('waiting for reactor shutdown')
        self.reactor_shutdown.wait()
        log.msg('reactor shut down')

    def webopen(self):
        if self.files_exist(['node.url', 'private/root_dir.cap']):
            def read_file(f):
                fh = file(f, 'rb')
                contents = fh.read().strip()
                fh.close()
                return contents
            nodeurl = read_file(os.path.join(self.basedir, 'node.url'))
            if nodeurl[-1] != "/":
                nodeurl += "/"
            root_dir = read_file(os.path.join(self.basedir, 'private/root_dir.cap'))
            url = nodeurl + "uri/%s/" % urllib.quote(root_dir)
            webbrowser.open(url)
        else:
            print 'files missing, not opening initial webish root page'

    def maybe_install_tahoe_script(self):
        path_candidates = ['/usr/local/bin', '~/bin', '~/Library/bin']
        env_path = map(os.path.expanduser, os.environ['PATH'].split(':'))
        if not sys.executable.endswith('/python'):
            print 'not installing tahoe script: unexpected sys.exe "%s"' % (sys.executable,)
            return
        for path_candidate in map(os.path.expanduser, env_path):
            tahoe_path = path_candidate + '/tahoe'
            if os.path.exists(tahoe_path):
                print 'not installing "tahoe": it already exists at "%s"' % (tahoe_path,)
                return
        for path_candidate in map(os.path.expanduser, path_candidates):
            if path_candidate not in env_path:
                print path_candidate, 'not in', env_path
                continue
            tahoe_path = path_candidate + '/tahoe'
            try:
                print 'trying to install "%s"' % (tahoe_path,)
                bin_path = (sys.executable[:-6] + 'Allmydata')
                script = TAHOE_SCRIPT % { 'exe': bin_path }
                f = file(tahoe_path, 'wb')
                f.write(script)
                f.close()
                mode = stat.S_IRUSR|stat.S_IXUSR|stat.S_IRGRP|stat.S_IXGRP|stat.S_IROTH|stat.S_IXOTH
                os.chmod(tahoe_path, mode)
                print 'installed "%s"' % (tahoe_path,)
                return
            except:
                print 'unable to write %s' % (tahoe_path,)
                traceback.print_exc()
        else:
            print 'no remaining candidate paths for installation of tahoe script'


def DisplayTraceback(message):
    xc = traceback.format_exception(*sys.exc_info())
    wx.MessageBox(u"%s\n (%s)"%(message,''.join(xc)), 'Error')

WEBOPEN_ID = wx.NewId()
ACCOUNT_PAGE_ID = wx.NewId()
MOUNT_ID = wx.NewId()

class SplashFrame(wx.Frame):
    def __init__(self):
        wx.Frame.__init__(self, None, -1, 'Allmydata')

        self.SetSizeHints(100, 100, 600, 800)
        self.SetIcon(amdicon.getIcon())
        self.Bind(wx.EVT_CLOSE, self.on_close)

        background = wx.Panel(self, -1)
        background.parent = self
        self.login_panel = SplashPanel(background, self.on_close)
        sizer = wx.BoxSizer(wx.VERTICAL)
        background_sizer = wx.BoxSizer(wx.VERTICAL)
        background_sizer.Add(self.login_panel, 1, wx.ALIGN_CENTER_HORIZONTAL | wx.ALL, 26)
        background.SetSizer(background_sizer)
        sizer.Add(background, 0, wx.EXPAND | wx.ALL, 0)
        self.SetSizer(sizer)
        self.SetAutoLayout(True)
        self.Fit()
        self.Layout()

    def on_close(self, event):
        self.Show(False)

class SplashPanel(wx.Panel):
    def __init__(self, parent, on_close):
        wx.Panel.__init__(self, parent, -1)
        self.parent = parent

        self.sizer = wx.BoxSizer(wx.VERTICAL)

        self.label = wx.StaticText(self, -1, 'Allmydata')
        font = self.label.GetFont()
        font.SetPointSize(26)
        self.label.SetFont(font)
        self.ver_label = wx.StaticText(self, -1, str(allmydata.__version__))
        self.ok = wx.Button(self, -1, 'Ok')
        self.Bind(wx.EVT_BUTTON, on_close, self.ok)
        self.sizer.Add(self.label, 0, wx.CENTER | wx.ALL, 2)
        self.sizer.Add(self.ver_label, 0, wx.CENTER | wx.ALL, 2)
        self.sizer.Add(wx.Size(42,42), 1, wx.EXPAND | wx.ALL, 2)
        self.sizer.Add(self.ok, 0, wx.CENTER | wx.ALL, 2)
        self.SetSizer(self.sizer)
        self.SetAutoLayout(True)


class MountFrame(wx.Frame):
    def __init__(self, app):
        wx.Frame.__init__(self, None, -1, 'Allmydata Mount Filesystem')

        self.SetSizeHints(100, 100, 600, 800)
        self.SetIcon(amdicon.getIcon())
        self.Bind(wx.EVT_CLOSE, self.on_close)

        background = wx.Panel(self, -1)
        background.parent = self
        self.mount_panel = MountPanel(background, self.on_close, app)
        sizer = wx.BoxSizer(wx.VERTICAL)
        background_sizer = wx.BoxSizer(wx.VERTICAL)
        background_sizer.Add(self.mount_panel, 1, wx.ALIGN_CENTER_HORIZONTAL | wx.ALL, 26)
        background.SetSizer(background_sizer)
        sizer.Add(background, 0, wx.EXPAND | wx.ALL, 0)
        self.SetSizer(sizer)
        self.SetAutoLayout(True)
        self.Fit()
        self.Layout()

    def on_close(self, event):
        self.Show(False)

class MountPanel(wx.Panel):
    def __init__(self, parent, on_close, app):
        wx.Panel.__init__(self, parent, -1)
        self.parent = parent
        self.app = app

        self.sizer = wx.BoxSizer(wx.VERTICAL)

        self.caps = self.find_dir_caps()

        self.label = wx.StaticText(self, -1, 'Allmydata Mount Filesystem')
        self.mnt_label = wx.StaticText(self, -1, 'Mount')
        self.cap_choice = wx.Choice(self, -1, (120, 64), choices=self.caps.keys())
        root_dir = self.cap_choice.FindString('root_dir')
        if root_dir != -1:
            self.cap_choice.SetSelection(root_dir)
        self.at_label = wx.StaticText(self, -1, 'at')
        self.mountpoint = wx.TextCtrl(self, -1, 'choose a mount dir', size=(256,22))
        self.mnt_browse = wx.Button(self, -1, 'Browse')
        mount_sizer = wx.BoxSizer(wx.HORIZONTAL)
        mount_sizer.Add(self.mnt_label, 0, wx.ALL, 4)
        mount_sizer.Add(self.cap_choice, 0, wx.ALL, 4)
        mount_sizer.Add(self.at_label, 0, wx.ALL, 4)
        mount_sizer.Add(self.mountpoint, 0, wx.ALL, 4)
        mount_sizer.Add(self.mnt_browse, 0, wx.ALL, 4)
        self.mount = wx.Button(self, -1, 'Mount')
        self.Bind(wx.EVT_BUTTON, self.on_mount, self.mount)
        #self.Bind(wx.EVT_CHOICE, self.on_choice, self.cap_choice)
        self.Bind(wx.EVT_BUTTON, self.on_mnt_browse, self.mnt_browse)
        self.sizer.Add(self.label, 0, wx.CENTER | wx.ALL, 2)
        self.sizer.Add(wx.Size(28,28), 1, wx.EXPAND | wx.ALL, 2)
        self.sizer.Add(mount_sizer, 0, wx.EXPAND | wx.ALL, 0)
        self.sizer.Add(wx.Size(28,28), 1, wx.EXPAND | wx.ALL, 2)
        self.sizer.Add(self.mount, 0, wx.CENTER | wx.ALL, 2)
        self.SetSizer(self.sizer)
        self.SetAutoLayout(True)

    def find_dir_caps(self):
        priv_dir = os.path.join(self.app.basedir, 'private')
        fs = os.listdir(priv_dir)
        caps = {}
        for f in fs:
            if not f.endswith('.cap'):
                continue
            try:
                log.msg('reading: %r' % (f,))
                fh = file(os.path.join(priv_dir, f), 'rb')
                cap = fh.read().strip()
                fh.close()
                uri = NewDirectoryURI.init_from_string(cap)
                caps[f[:-4]] = cap
            except:
                log.msg('failed to read dir cap from "%s"' % (f,))
                log.err()
        return caps

    #def on_choice(self, event):
        #choice = event.GetString()
        #log.msg('chose dir: %s' % (choice,))

    def on_mount(self, event):
        mountpoint = str(self.mountpoint.GetValue())
        if not os.path.isdir(mountpoint):
            wx.MessageBox(u'"%s" is not a directory' % (mountpoint,))
        else:
            cap_name = self.cap_choice.GetStringSelection()
            self.do_mount(cap_name, mountpoint)

    def on_mnt_browse(self, event):
        dlg = wx.DirDialog(self, "Choose a Mountpoint Directory:",
                           style=wx.DD_DEFAULT_STYLE|wx.DD_NEW_DIR_BUTTON)
        if dlg.ShowModal() == wx.ID_OK:
            mountpoint = dlg.GetPath()
            self.mountpoint.SetValue(mountpoint)
        dlg.Destroy()

    def do_mount(self, cap_name, mountpoint):
        log.msg('do_mount(%r, %r)' % (cap_name, mountpoint))
        log.msg('sys.exec = %r' % (sys.executable,))
        if not sys.executable.endswith('Allmydata.app/Contents/MacOS/python'):
            log.msg("can't find allmydata.app: sys.executable = %r" % (sys.executable,))
            wx.MessageBox("Can't determine location of Allmydata.app")
            self.parent.parent.Show(False)
            return
        bin_path = sys.executable[:-6] + 'Allmydata'
        log.msg('%r exists: %r' % (bin_path, os.path.exists(bin_path),))

        foptions = []
        foptions.append('-ovolname=%s' % (cap_name,))

        timeout = DEFAULT_FUSE_TIMEOUT
        # [ ] TODO: make this configurable
        if timeout:
            foptions.append('-odaemon_timeout=%d' % (timeout,))

        icns_path = os.path.join(self.app.basedir, 'private', cap_name+'.icns')
        if os.path.exists(icns_path):
            foptions.append('-ovolicon=%s' % (icns_path,))

        command = [bin_path, 'fuse', cap_name] + foptions + [mountpoint]
        log.msg('spawning command %r' % (command,))
        proc = subprocess.Popen(command,
                                cwd=self.app.basedir,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        log.msg('spawned process, pid %s' % (proc.pid,))
        wx.FutureCall(4096, self.check_mount, proc)
        self.parent.parent.Show(False)

    def check_mount(self, proc):
        message = [ 'pid: %s' % (proc.pid,),
                    'ret: %s' % (proc.returncode,),
                    'stdout:\n%s' % (proc.stdout.read(),),
                    'stderr:\n%s' % (proc.stderr.read(),),
                    ]
        log.msg('\n'.join(['spawned process:'] + message))

class MacGuiApp(wx.App):
    def __init__(self, app):
        self.app = app
        wx.App.__init__(self)

    def OnInit(self):
        try:
            self.frame = SplashFrame()
            self.frame.Show(True)
            self.SetTopWindow(self.frame)

            wx.FutureCall(4096, self.on_timer, None)

            self.mount_frame = MountFrame(self.app)

            self.setup_dock_icon()
            menubar = self.setup_app_menu(self.frame)
            self.frame.SetMenuBar(menubar)

            return True
        except:
            DisplayTraceback('exception on startup')
            sys.exit()

    def on_timer(self, event):
        self.frame.Show(False)

    def setup_dock_icon(self):
        self.tbicon = wx.TaskBarIcon()
        #self.tbicon.SetIcon(amdicon.getIcon(), "Allmydata")
        wx.EVT_TASKBAR_RIGHT_UP(self.tbicon, self.on_dock_menu)

    def setup_app_menu(self, frame):
        menubar = wx.MenuBar()
        file_menu = wx.Menu()
        item = file_menu.Append(WEBOPEN_ID, text='Open Web Root')
        frame.Bind(wx.EVT_MENU, self.on_webopen, item)
        item = file_menu.Append(ACCOUNT_PAGE_ID, text='Open Account Page')
        frame.Bind(wx.EVT_MENU, self.on_account_page, item)
        item = file_menu.Append(MOUNT_ID, text='Mount Filesystem')
        frame.Bind(wx.EVT_MENU, self.on_mount, item)
        item = file_menu.Append(wx.ID_ABOUT, text='About')
        frame.Bind(wx.EVT_MENU, self.on_about, item)
        item = file_menu.Append(wx.ID_EXIT, text='Quit')
        frame.Bind(wx.EVT_MENU, self.on_quit, item)
        menubar.Append(file_menu, 'File')
        return menubar

    def on_dock_menu(self, event):
        dock_menu = wx.Menu()
        item = dock_menu.Append(wx.NewId(), text='About')
        self.tbicon.Bind(wx.EVT_MENU, self.on_about, item)
        item = dock_menu.Append(WEBOPEN_ID, text='Open Web Root')
        self.tbicon.Bind(wx.EVT_MENU, self.on_webopen, item)
        item = dock_menu.Append(ACCOUNT_PAGE_ID, text='Open Account Page')
        self.tbicon.Bind(wx.EVT_MENU, self.on_account_page, item)
        item = dock_menu.Append(MOUNT_ID, text='Mount Filesystem')
        self.tbicon.Bind(wx.EVT_MENU, self.on_mount, item)
        self.tbicon.PopupMenu(dock_menu)

    def on_about(self, event):
        self.frame.Show(True)

    def on_quit(self, event):
        self.ExitMainLoop()

    def on_webopen(self, event):
        self.app.webopen()

    def on_account_page(self, event):
        webbrowser.open(DEFAULT_SERVER_URL + ACCOUNT_PAGE)

    def on_mount(self, event):
        self.mount_frame.Show(True)

