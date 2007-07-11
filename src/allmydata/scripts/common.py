
import os
from twisted.python import usage


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
                self.basedirs.append(".")
            if len(args) > 0:
                self.basedirs.append(args[0])
            if len(args) > 1:
                raise usage.UsageError("I wasn't expecting so many arguments")

class NoDefaultBasedirMixin(BasedirMixin):
    def parseArgs(self, *args):
        # create-client won't default to --basedir=.
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

