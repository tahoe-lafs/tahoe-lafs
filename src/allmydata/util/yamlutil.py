"""
Ported to Python 3.
"""

import yaml

def safe_load(f):
    return yaml.safe_load(f)

def safe_dump(obj):
    return yaml.safe_dump(obj)
