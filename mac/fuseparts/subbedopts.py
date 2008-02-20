#
#    Copyright (C) 2006  Csaba Henk  <csaba.henk@creo.hu>
#
#    This program can be distributed under the terms of the GNU LGPL.
#    See the file COPYING.
#

from optparse import Option, OptionParser, OptParseError, OptionConflictError
from optparse import HelpFormatter, IndentedHelpFormatter, SUPPRESS_HELP
from fuseparts.setcompatwrap import set

##########
###
###  Generic suboption parsing stuff.
###
##########



class SubOptsHive(object):
    """
    Class for collecting unhandled suboptions.
    """

    def __init__(self):

        self.optlist = set()
        self.optdict = {}

    def _str_core(self):

        sa = []
        for k, v in self.optdict.iteritems():
             sa.append(str(k) + '=' + str(v))

        ra = (list(self.optlist) + sa) or ["(none)"]
        ra.sort()
        return ra 

    def __str__(self):
        return "< opts: " + ", ".join(self._str_core()) + " >"

    def canonify(self):
        """
        Transform self to an equivalent canonical form:
        delete optdict keys with False value, move optdict keys
        with True value to optlist, stringify other values.
        """

        for k, v in self.optdict.iteritems():
            if v == False:
                self.optdict.pop(k)
            elif v == True:
                self.optdict.pop(k)
                self.optlist.add(v)
            else:
                self.optdict[k] = str(v)

    def filter(self, other):
        """
        Throw away those options which are not in the other one.
        Returns a new instance with the rejected options.
        """

        self.canonify()
        other.canonify()

        rej = self.__class__()
        rej.optlist = self.optlist.difference(other.optlist)
        self.optlist.difference_update(rej.optlist)
        for x in self.optdict.copy():
             if x not in other.optdict:
                 self.optdict.pop(x)
                 rej.optdict[x] = None

        return rej

    def add(self, opt, val=None):
        """Add a suboption."""

        ov = opt.split('=', 1)
        o = ov[0]
        v = len(ov) > 1 and ov[1] or None

        if (v):
            if val != None:
                raise AttributeError, "ambiguous option value"
            val = v

        if val == False:
            return

        if val in (None, True):
            self.optlist.add(o)
        else:
            self.optdict[o] = val



class SubbedOpt(Option):
    """
    `Option` derivative enhanced with the attribute of being a suboption of
     some other option (like ``foo`` and ``bar`` for ``-o`` in ``-o foo,bar``).
    """

    ATTRS = Option.ATTRS + ["subopt", "subsep", "subopts_hive"]
    ACTIONS = Option.ACTIONS + ("store_hive",)
    STORE_ACTIONS = Option.STORE_ACTIONS + ("store_hive",)
    TYPED_ACTIONS = Option.TYPED_ACTIONS + ("store_hive",)

    def __init__(self, *opts, **attrs):

       self.subopt_map = {}

       if "subopt" in attrs:
           self._short_opts = []
           self._long_opts = []
           self._set_opt_strings(opts)
           self.baseopt = self._short_opts[0] or self._long_opts[0]
           opts = ()

       Option.__init__(self, *opts, **attrs)

    def __str__(self):
        pf = ""
        if hasattr(self, "subopt") and self.subopt:
            pf = " %s...,%s,..." % (self.baseopt, self.subopt)
        return Option.__str__(self) + pf

    def _check_opt_strings(self, opts):
        return opts

    def _check_dest(self):
        try:
            Option._check_dest(self)
        except IndexError:
            if self.subopt:
                self.dest = "__%s__%s" % (self.baseopt, self.subopt)
                self.dest = self.dest.replace("-", "")
            else:
                raise

    def get_opt_string(self):
        if hasattr(self, 'subopt'):
            return self.subopt
        else:
            return Option.get_opt_string(self)

    def take_action(self, action, dest, opt, value, values, parser):
        if action == "store_hive":
            if not hasattr(values, dest) or getattr(values, dest) == None:
                 if hasattr(self, "subopts_hive") and self.subopts_hive:
                     hive = self.subopts_hive
                 else:
                     hive = parser.hive_class()
                 setattr(values, dest, hive)
            for o in value.split(self.subsep or ","):
                oo = o.split('=')
                ok = oo[0]
                ov = None
                if (len(oo) > 1):
                    ov = oo[1]
                if ok in self.subopt_map:
                    self.subopt_map[ok].process(ok, ov, values, parser)
                else:
                    getattr(values, dest).add(*oo)
            return
        Option.take_action(self, action, dest, opt, value, values, parser)

    def register_sub(self, o):
        """Register argument a suboption for `self`."""

        if o.subopt in self.subopt_map:
            raise OptionConflictError(
              "conflicting suboption handlers for `%s'" % o.subopt,
              o)
        self.subopt_map[o.subopt] = o

    CHECK_METHODS = []
    for m in Option.CHECK_METHODS:
        #if not m == Option._check_dest:
        if not m.__name__ == '_check_dest':
            CHECK_METHODS.append(m)
    CHECK_METHODS.append(_check_dest)



class SubbedOptFormatter(HelpFormatter):

    def format_option_strings(self, option):
        if hasattr(option, "subopt") and option.subopt:
            res = '-o ' + option.subopt
            if option.takes_value():
                res += "="
                res += option.metavar or 'FOO'
            return res

        return HelpFormatter.format_option_strings(self, option)



class SubbedOptIndentedFormatter(IndentedHelpFormatter, SubbedOptFormatter):

    def format_option_strings(self, option):
        return SubbedOptFormatter.format_option_strings(self, option)



class SubbedOptParse(OptionParser):
    """
    This class alters / enhances `OptionParser` with *suboption handlers*.

    That is, calling `sop.add_option('-x', subopt=foo)` installs a handler
    which will be triggered if there is ``-x foo`` in the command line being
    parsed (or, eg., ``-x foo,bar``).

    Moreover, ``-x`` implicitly gets a handler which collects the unhandled
    suboptions of ``-x`` into a `SubOptsHive` instance (accessible post festam
    via the `x` attribute of the returned Values object). (The only exception
    is when ``-x`` has *explicitly*  been added with action ``store_hive``.
    This opens up the possibility of customizing the ``-x`` handler at some
    rate.)

    Suboption handlers have all the nice features of normal option handlers,
    eg. they are displayed in the automatically generated help message
    (and can have their own help info).
    """

    def __init__(self, *args, **kw):

         if not 'formatter' in kw:
             kw['formatter'] = SubbedOptIndentedFormatter()
         if not 'option_class' in kw:
             kw['option_class'] = SubbedOpt
         if 'hive_class' in kw:
             self.hive_class = kw.pop('hive_class')
         else:
             self.hive_class = SubOptsHive

         OptionParser.__init__(self, *args, **kw)

    def add_option(self, *args, **kwargs):
        if 'action' in kwargs and kwargs['action'] == 'store_hive':
            if 'subopt' in kwargs:
                raise OptParseError(
                  """option can't have a `subopt' attr and `action="store_hive"' at the same time""")
            if not 'type' in kwargs:
                kwargs['type'] = 'string'
        elif 'subopt' in kwargs:
            o = self.option_class(*args, **kwargs)

            oo = self.get_option(o.baseopt)
            if oo:
                if oo.action != "store_hive":
                    raise OptionConflictError(
                      "can't add subopt as option has already a handler that doesn't do `store_hive'",
                      oo)
            else:
                self.add_option(o.baseopt, action='store_hive',
                                metavar="sub1,[sub2,...]")
                oo = self.get_option(o.baseopt)

            oo.register_sub(o)

            args = (o,)
            kwargs = {}

        return OptionParser.add_option(self, *args, **kwargs)
