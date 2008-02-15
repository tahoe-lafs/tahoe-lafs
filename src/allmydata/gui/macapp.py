
import operator
import os
import stat
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
from allmydata.gui.confwiz import ConfWizApp, ACCOUNT_PAGE
import amdicon


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
            app = ConfWizApp()
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
            guiapp = MacGuiApp(app=self)
            guiapp.MainLoop()
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
        c = client.Client(self.basedir)
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
                bin_path = (sys.executable[:-6] + 'Allmydata Tahoe').replace(' ', '\\ ')
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

class SplashFrame(wx.Frame):
    def __init__(self):
        wx.Frame.__init__(self, None, -1, 'Allmydata Tahoe')

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

        self.label = wx.StaticText(self, -1, 'Allmydata Tahoe')
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


class MacGuiApp(wx.App):
    def __init__(self, app):
        wx.App.__init__(self)
        self.app = app

    def OnInit(self):
        try:
            self.frame = SplashFrame()
            self.frame.Show(True)
            self.SetTopWindow(self.frame)

            wx.FutureCall(4096, self.on_timer, None)

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
        self.tbicon.SetIcon(amdicon.getIcon(), "Allmydata Tahoe")
        wx.EVT_TASKBAR_RIGHT_UP(self.tbicon, self.on_dock_menu)

    def setup_app_menu(self, frame):
        menubar = wx.MenuBar()
        file_menu = wx.Menu()
        item = file_menu.Append(WEBOPEN_ID, text='Open Web Root')
        frame.Bind(wx.EVT_MENU, self.on_webopen, item)
        item = file_menu.Append(ACCOUNT_PAGE_ID, text='Open Account Page')
        frame.Bind(wx.EVT_MENU, self.on_account_page, item)
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
        self.tbicon.PopupMenu(dock_menu)

    def on_about(self, event):
        self.frame.Show(True)

    def on_quit(self, event):
        self.ExitMainLoop()

    def on_webopen(self, event):
        self.app.webopen()

    def on_account_page(self, event):
        webbrowser.open(ACCOUNT_PAGE)


def main(argv):
    if len(argv) == 1:
        # then we were given no args; do default mac node startup
        sys.exit(run_macapp())
    else:
        # given any cmd line args, do 'tahoe' cli behaviour
        from allmydata.scripts import runner
        sys.exit(runner.runner(argv[1:], install_node_control=False))

if __name__ == '__main__':
    main(sys.argv)

