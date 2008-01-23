import pkgreshook # override the pkg_resources zip provider for py2app deployment
pkgreshook.install() # this is done before nevow is imported by depends
import depends # import dependencies so that py2exe finds them
_junk = depends # appease pyflakes

import sys
import os
import stat
import traceback

TRY_TO_INSTALL_TAHOE_SCRIPT = True
TAHOE_SCRIPT = '''#!/bin/bash
if [ "x${@}x" == "xx" ]
then
    %(exe)s --help
else
    %(exe)s "${@}"
fi
'''

def run_default_node():
    import operator

    basedir = os.path.expanduser('~/.tahoe')
    if not os.path.isdir(basedir):
        app_supp = os.path.expanduser('~/Library/Application Support/Allmydata Tahoe/')
        if not os.path.isdir(app_supp):
            os.makedirs(app_supp)
        os.symlink(app_supp, basedir)

    if not os.path.exists(os.path.join(basedir, 'webport')):
        f = file(os.path.join(basedir, 'webport'), 'wb')
        f.write('8123')
        f.close()

    def files_exist(file_list):
        extant_conf = [ os.path.exists(os.path.join(basedir, f)) for f in file_list ]
        return reduce(operator.__and__, extant_conf)

    def is_config_incomplete():
        necessary_conf_files = ['introducer.furl', 'private/root_dir.cap']
        need_config = not files_exist(necessary_conf_files)
        if need_config:
            print 'some config is missing from basedir (%s): %s' % (basedir, necessary_conf_files)
        return need_config

    if is_config_incomplete():
        #import wx
        from confwiz import ConfWizApp
        app = ConfWizApp()
        app.MainLoop()

    if is_config_incomplete():
        print 'config still incomplete; confwiz cancelled, exiting'
        return 1

    from twisted.internet import reactor
    from twisted.python import log, logfile
    from allmydata import client
    # set up twisted logging. this will become part of the node rsn.
    logdir = os.path.join(basedir, 'logs')
    if not os.path.exists(logdir):
        os.makedirs(logdir)
    lf = logfile.LogFile('tahoesvc.log', logdir)
    log.startLogging(lf)

    def webopen():
        if files_exist(['node.url', 'private/root_dir.cap']):
            def read_file(f):
                fh = file(f, 'rb')
                contents = fh.read().strip()
                fh.close()
                return contents
            import urllib, webbrowser
            nodeurl = read_file(os.path.join(basedir, 'node.url'))
            if nodeurl[-1] != "/":
                nodeurl += "/"
            root_dir = read_file(os.path.join(basedir, 'private/root_dir.cap'))
            url = nodeurl + "uri/%s/" % urllib.quote(root_dir)
            webbrowser.open(url)
        else:
            print 'files missing, not opening initial webish root page'

    def maybe_install_tahoe_script():
        path_candidates = ['/usr/local/bin', '~/bin', '~/Library/bin']
        env_path = map(os.path.expanduser, os.environ['PATH'].split(':'))
        if not sys.executable.endswith('/python'):
            print 'not installing tahoe script: unexpected sys.exe "%s"' % (sys.executable,)
            return
        for path_candidate in map(os.path.expanduser, path_candidates):
            if path_candidate not in env_path:
                print path_candidate, 'not in', env_path
                continue
            tahoe_path = path_candidate + '/tahoe'
            try:
                if os.path.exists(tahoe_path):
                    print 'not installing "%s": it already exists' % (tahoe_path,)
                    return
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

    if TRY_TO_INSTALL_TAHOE_SCRIPT:
        maybe_install_tahoe_script()

    # run the node itself
    os.chdir(basedir)
    c = client.Client(basedir)
    reactor.callLater(0, c.startService) # after reactor startup
    reactor.callLater(4, webopen) # give node a chance to connect before loading root dir
    reactor.run()

    return 0



def main(argv):
    if len(argv) == 1:
        # then we were given no args; do default mac node startup
        sys.exit(run_default_node())
    else:
        # given any cmd line args, do 'tahoe' cli behaviour
        from allmydata.scripts import runner
        sys.exit(runner.runner(argv[1:], install_node_control=False))

if __name__ == '__main__':
    main(sys.argv)

