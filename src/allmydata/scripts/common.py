
import os, sys
from twisted.python import usage


class BaseOptions:
    optFlags = [
        ["quiet", "q", "Operate silently."],
        ["version", "V", "Display version numbers and exit."],
        ]

    def opt_version(self):
        from twisted import copyright
        import allmydata, zfec, foolscap, pycryptopp
        print "Twisted version:", copyright.version
        print "Foolscap version:", foolscap.__version__
        print "zfec version:", zfec.__version__
        print "allmydata version:", allmydata.__version__
        print "pycryptopp version:", pycryptopp.__version__
        sys.exit(0)


class BasedirMixin:
    optFlags = [
        ["multiple", "m", "allow multiple basedirs to be specified at once"],
        ]

    def postOptions(self):
        if not self.basedirs:
            raise usage.UsageError("<basedir> parameter is required")
        if self['basedir']:
            del self['basedir']
        self['basedirs'] = [os.path.abspath(os.path.expanduser(b))
                            for b in self.basedirs]

    def parseArgs(self, *args):
        self.basedirs = []
        if self['basedir']:
            self.basedirs.append(self['basedir'])
        if self['multiple']:
            self.basedirs.extend(args)
        else:
            if len(args) == 0 and not self.basedirs:
                self.basedirs.append(os.path.expanduser("~/.tahoe"))
            if len(args) > 0:
                self.basedirs.append(args[0])
            if len(args) > 1:
                raise usage.UsageError("I wasn't expecting so many arguments")

class NoDefaultBasedirMixin(BasedirMixin):
    def parseArgs(self, *args):
        # create-client won't default to --basedir=~/.tahoe
        self.basedirs = []
        if self['basedir']:
            self.basedirs.append(self['basedir'])
        if self['multiple']:
            self.basedirs.extend(args)
        else:
            if len(args) > 0:
                self.basedirs.append(args[0])
            if len(args) > 1:
                raise usage.UsageError("I wasn't expecting so many arguments")
        if not self.basedirs:
            raise usage.UsageError("--basedir must be provided")


