import sys
reload(sys)
sys.setdefaultencoding("utf-8")

import win32serviceutil
import win32service
import win32event
import win32evtlogutil

import os
import thread
import time
import traceback

# this logging should go away once service startup is considered debugged.
logfilehandle = file('c:\\tahoe_service.log', 'ab+')
def logmsg(msg):
    logfilehandle.write("%s: %s\r\n" % (time.strftime('%Y%m%d_%H%M%S'), msg))
    logfilehandle.flush()
logmsg('service loaded')

#
# Now with some bootstrap util functions in place, let's try and init things:
try:
    import pkgreshook # override the pkg_resources zip provider for py2exe deployment
    pkgreshook.install() # this is done before nevow is imported

    logmsg('loading base dir')
    from allmydata.windows import registry
    basedir = registry.get_base_dir_path()
    logmsg("got base dir (%s)" % (basedir,))
    if not basedir:
        regpth = "%s : %s " % (registry._AMD_KEY, registry._BDIR_KEY)
        raise RuntimeError('"%s" not set in registry' % (regpth,))
    os.chdir(basedir)
    logmsg("chdir(%s)" % (basedir,))
except:
    logmsg("exception")
    traceback.print_exc(None, logfilehandle)
    logfilehandle.flush()
    logfilehandle.close()
    raise

class Tahoe(win32serviceutil.ServiceFramework):
    _svc_name_ = "Tahoe"
    _svc_display_name_ = "Allmydata Tahoe Node"
    def __init__(self, args):
        logmsg("init")
        try:
            # The exe-file has messages for the Event Log Viewer.
            # Register the exe-file as event source.
            #
            # Probably it would be better if this is done at installation time,
            # so that it also could be removed if the service is uninstalled.
            # Unfortunately it cannot be done in the 'if __name__ == "__main__"'
            # block below, because the 'frozen' exe-file does not run this code.
            #
            logmsg("service start")
            win32evtlogutil.AddSourceToRegistry(self._svc_display_name_,
                                                sys.executable,
                                                "Application")
            win32serviceutil.ServiceFramework.__init__(self, args)
            self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        except:
            try:
                logmsg("exception")
                traceback.print_exc(None, logfilehandle)
                logfilehandle.flush()
                logfilehandle.close()
            except:
                os.abort()

    def SvcStop(self):
        logmsg("service stop")
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self):
        try:
            logmsg("service run")
            import servicemanager
            # Write a 'started' event to the event log...
            win32evtlogutil.ReportEvent(self._svc_display_name_,
                                        servicemanager.PYS_SERVICE_STARTED,
                                        0, # category
                                        servicemanager.EVENTLOG_INFORMATION_TYPE,
                                        (self._svc_name_, ''))

            reactor_type = registry.get_registry_value('reactor')
            if reactor_type == 'iocp':
                from twisted.internet import iocpreactor
                iocpreactor.install()
            else:
                from twisted.internet import selectreactor
                selectreactor.install()
            from twisted.internet import reactor

            if os.path.exists('DISABLE_STARTUP'):
                logmsg("DISABLE_STARTUP exists: exiting")
            else:
                logmsg("runing reactorthread")

                # launch main thread...
                thread.start_new_thread(self.launch_node, ())

                # ...and block until service stop request
                win32event.WaitForSingleObject(self.hWaitStop, win32event.INFINITE)

                logmsg("wake up")

                reactor.callFromThread(reactor.stop)

                time.sleep(2) # give the node/reactor a chance to cleanup

            # and write a 'stopped' event to the event log.
            win32evtlogutil.ReportEvent(self._svc_display_name_,
                                        servicemanager.PYS_SERVICE_STOPPED,
                                        0, # category
                                        servicemanager.EVENTLOG_INFORMATION_TYPE,
                                        (self._svc_name_, ''))
        except:
            try:
                logmsg("exception")
                traceback.print_exc(None, logfilehandle)
                logfilehandle.flush()
                logfilehandle.close()
            except:
                os.abort()

    def launch_node(self):
        try:
            logmsg("main thread startup")

            import depends # import dependencies so that py2exe finds them
            _junk = depends # appease pyflakes

            from twisted.internet import reactor
            from twisted.python import log, logfile
            from allmydata import client

            # set up twisted logging. this will become part of the node rsn.
            logdir = os.path.join(basedir, 'logs')
            if not os.path.exists(logdir):
                os.makedirs(logdir)
            lf = logfile.LogFile('tahoesvc.log', logdir)
            log.startLogging(lf)

            # run the node itself
            c = client.Client(basedir)
            reactor.callLater(0, c.startService) # after reactor startup
            reactor.run(installSignalHandlers=False)

            logmsg("main thread shutdown")
        except:
            logmsg("exception")
            traceback.print_exc(None, logfilehandle)
            logfilehandle.flush()
            os.abort()

if __name__ == '__main__':
    logmsg("service main")
    win32serviceutil.HandleCommandLine(Tahoe)

