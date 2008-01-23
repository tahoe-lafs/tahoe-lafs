import pkgreshook # override the pkg_resources zip provider for py2app deployment
pkgreshook.install() # this is done before nevow is imported by depends
import depends # import dependencies so that py2exe finds them
_junk = depends # appease pyflakes

import sys
import os

def run_default_node():
    import operator
    import os

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

