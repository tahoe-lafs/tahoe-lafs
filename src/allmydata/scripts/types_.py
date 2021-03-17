"""
Type definitions used by modules in this package.
"""

# Python 3 only

from typing import List, Tuple, Type, Sequence, Any
from allmydata.scripts.common import BaseOptions


# Historically, subcommands were implemented as lists, but due to a
# [designed contraint in mypy](https://stackoverflow.com/a/52559625/70170),
# a Tuple is required.
SubCommand = Tuple[str, None, Type[BaseOptions], str]

SubCommands = List[SubCommand]

Parameters = List[Sequence[Any]]
