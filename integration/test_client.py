import sys
import time
import shutil
from os import mkdir, unlink, listdir
from os.path import join, exists

import util

import pytest


def test_alice_status(alice):
    """
    Add two uploads and ensure that the status endpoint gives us sensible data.

    We only check the *ratio* of the filesizes given, as they may not
    correspond to the actual on-disk size (but, their ratio should be
    the same).
    """

    print(alice)
    print(dir(alice))
