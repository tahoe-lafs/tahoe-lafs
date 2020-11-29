from typing import List, Tuple, Type
from allmydata.scripts.common import BaseOptions


# Historically, subcommands were implemented as lists, but due to a
# [designed contraint in mypy](https://stackoverflow.com/a/52559625/70170),
# a Tuple is required.
SubCommand = Tuple[str, None, Type[BaseOptions], str]

SubCommands = List[SubCommand]
