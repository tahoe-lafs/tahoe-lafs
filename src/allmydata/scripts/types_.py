"""
Type definitions used by modules in this package.
"""

from typing import List, Tuple, Type, Sequence, Any
from twisted.python.usage import Options


# Historically, subcommands were implemented as lists, but due to a
# [designed contraint in mypy](https://stackoverflow.com/a/52559625/70170),
# a Tuple is required.
SubCommand = Tuple[str, None, Type[Options], str]

SubCommands = List[SubCommand]

Parameters = List[Sequence[Any]]

Flags = List[Tuple[str, None, str]]
