
import os.path

def sibling(filename):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
