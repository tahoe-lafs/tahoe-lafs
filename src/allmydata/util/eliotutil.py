"""
Tools aimed at the interaction between Tahoe-LAFS implementation and Eliot.

Ported to Python 3.
"""

__all__ = [
    "MemoryLogger",
    "inline_callbacks",
    "eliot_logging_service",
    "opt_eliot_destination",
    "opt_help_eliot_destinations",
    "validateInstanceOf",
    "validateSetMembership",
    "capture_logging",
]

from sys import (
    stdout,
)
from functools import wraps
from logging import (
    INFO,
    Handler,
    getLogger,
)
from json import loads

from six import ensure_text
from zope.interface import (
    implementer,
)

import attr
from attr.validators import (
    optional,
    provides,
)
from twisted.internet import reactor
from eliot import (
    ILogger,
    Message,
    FileDestination,
    write_traceback,
    start_action,
)
from eliot.testing import (
    MemoryLogger,
    capture_logging,
)

from eliot._validation import (
    ValidationError,
)
from eliot.twisted import (
    DeferredContext,
    inline_callbacks,
)
from eliot.logwriter import ThreadedWriter
from twisted.python.usage import (
    UsageError,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.python.logfile import (
    LogFile,
)
from twisted.logger import (
    ILogObserver,
    eventAsJSON,
    globalLogPublisher,
)
from twisted.internet.defer import (
    maybeDeferred,
)
from twisted.application.service import MultiService

from .jsonbytes import AnyBytesJSONEncoder


def validateInstanceOf(t):
    """
    Return an Eliot validator that requires values to be instances of ``t``.
    """
    def validator(v):
        if not isinstance(v, t):
            raise ValidationError("{} not an instance of {}".format(v, t))
    return validator

def validateSetMembership(s):
    """
    Return an Eliot validator that requires values to be elements of ``s``.
    """
    def validator(v):
        if v not in s:
            raise ValidationError("{} not in {}".format(v, s))
    return validator

def eliot_logging_service(reactor, destinations):
    """
    Parse the given Eliot destination descriptions and return an ``IService``
    which will add them when started and remove them when stopped.

    See ``--help-eliot-destinations`` for details about supported
    destinations.
    """
    return _EliotLogging(destinations=list(
        get_destination(reactor)
        for get_destination
        in destinations
    ))


# An Options-based argument parser for configuring Eliot logging.  Set this as
# a same-named attribute on your Options subclass.
def opt_eliot_destination(self, description):
    """
    Add an Eliot logging destination.  May be given more than once.
    """
    try:
        destination = _parse_destination_description(description)
    except Exception as e:
        raise UsageError(str(e))
    else:
        self.setdefault("destinations", []).append(destination)


def opt_help_eliot_destinations(self):
    """
    Emit usage information for --eliot-destination.
    """
    print(
        "Available destinations:\n"
        # Might want to generate this from some metadata someday but we just
        # have one hard-coded destination type now, it's easier to hard-code
        # the help.
        "\tfile:<path>[,rotate_length=<bytes>][,max_rotated_files=<count>]\n"
        "\tSensible defaults are supplied for rotate_length and max_rotated_files\n"
        "\tif they are not given.\n",
        file=self.stdout,
    )
    raise SystemExit(0)


class _EliotLogging(MultiService):
    """
    A service which adds stdout as an Eliot destination while it is running.
    """
    def __init__(self, destinations):
        """
        :param list destinations: The Eliot destinations which will is added by this
            service.
        """
        MultiService.__init__(self)
        for destination in destinations:
            service = ThreadedWriter(destination, reactor)
            service.setServiceParent(self)

    def startService(self):
        self.stdlib_cleanup = _stdlib_logging_to_eliot_configuration(getLogger())
        self.twisted_observer = _TwistedLoggerToEliotObserver()
        globalLogPublisher.addObserver(self.twisted_observer)
        return MultiService.startService(self)


    def stopService(self):
        globalLogPublisher.removeObserver(self.twisted_observer)
        self.stdlib_cleanup()
        return MultiService.stopService(self)


@implementer(ILogObserver)
@attr.s(frozen=True)
class _TwistedLoggerToEliotObserver(object):
    """
    An ``ILogObserver`` which re-publishes events as Eliot messages.
    """
    logger = attr.ib(default=None, validator=optional(provides(ILogger)))

    def _observe(self, event):
        flattened = loads(eventAsJSON(event))
        # We get a timestamp from Eliot.
        flattened.pop(u"log_time")
        # This is never serializable anyway.  "Legacy" log events (from
        # twisted.python.log) don't have this so make it optional.
        flattened.pop(u"log_logger", None)

        Message.new(
            message_type=u"eliot:twisted",
            **flattened
        ).write(self.logger)


    # The actual ILogObserver interface uses this.
    __call__ = _observe


class _StdlibLoggingToEliotHandler(Handler):
    def __init__(self, logger=None):
        Handler.__init__(self)
        self.logger = logger

    def emit(self, record):
        Message.new(
            message_type=u"eliot:stdlib",
            log_level=record.levelname,
            logger=record.name,
            message=record.getMessage()
        ).write(self.logger)

        if record.exc_info:
            write_traceback(
                logger=self.logger,
                exc_info=record.exc_info,
            )


def _stdlib_logging_to_eliot_configuration(stdlib_logger, eliot_logger=None):
    """
    Add a handler to ``stdlib_logger`` which will relay events to
    ``eliot_logger`` (or the default Eliot logger if ``eliot_logger`` is
    ``None``).
    """
    handler = _StdlibLoggingToEliotHandler(eliot_logger)
    handler.set_name(u"eliot")
    handler.setLevel(INFO)
    stdlib_logger.addHandler(handler)
    return lambda: stdlib_logger.removeHandler(handler)


class _DestinationParser(object):
    def parse(self, description):
        description = ensure_text(description)

        try:
            kind, args = description.split(u":", 1)
        except ValueError:
            raise ValueError(
                u"Eliot destination description must be formatted like "
                u"<kind>:<args>."
            )
        try:
            parser = getattr(self, u"_parse_{}".format(kind))
        except AttributeError:
            raise ValueError(
                u"Unknown destination description: {}".format(description)
            )
        else:
            return parser(kind, args)

    def _get_arg(self, arg_name, default, arg_list):
        return dict(
            arg.split(u"=", 1)
            for arg
            in arg_list
        ).get(
            arg_name,
            default,
        )

    def _parse_file(self, kind, arg_text):
        # Reserve the possibility of an escape character in the future.  \ is
        # the standard choice but it's the path separator on Windows which
        # pretty much ruins it in this context.  Most other symbols already
        # have some shell-assigned meaning which makes them treacherous to use
        # in a CLI interface.  Eliminating all such dangerous symbols leaves
        # approximately @.
        if u"@" in arg_text:
            raise ValueError(
                u"Unsupported escape character (@) in destination text ({!r}).".format(arg_text),
            )
        arg_list = arg_text.split(u",")
        path_name = arg_list.pop(0)
        if path_name == "-":
            get_file = lambda: stdout
        else:
            path = FilePath(path_name)
            rotate_length = int(self._get_arg(
                u"rotate_length",
                1024 * 1024 * 1024,
                arg_list,
            ))
            max_rotated_files = int(self._get_arg(
                u"max_rotated_files",
                10,
                arg_list,
            ))
            def get_file():
                path.parent().makedirs(ignoreExistingDirectory=True)
                return LogFile(
                    path.basename(),
                    path.dirname(),
                    rotateLength=rotate_length,
                    maxRotatedFiles=max_rotated_files,
                )
        return lambda reactor: FileDestination(get_file(), encoder=AnyBytesJSONEncoder)


_parse_destination_description = _DestinationParser().parse

def log_call_deferred(action_type):
    """
    Like ``eliot.log_call`` but for functions which return ``Deferred``.
    """
    def decorate_log_call_deferred(f):
        @wraps(f)
        def logged_f(*a, **kw):
            # Use the action's context method to avoid ending the action when
            # the `with` block ends.
            with start_action(action_type=action_type).context():
                # Use addActionFinish so that the action finishes when the
                # Deferred fires.
                d = maybeDeferred(f, *a, **kw)
                return DeferredContext(d).addActionFinish()
        return logged_f
    return decorate_log_call_deferred
