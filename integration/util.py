import time
from os.path import exists


def await_file_contents(path, contents, timeout=10):
    start_time = time.time()
    while time.time() - start_time < timeout:
        print("  waiting for '{}'".format(path))
        if exists(path):
            with open(path, 'r') as f:
                current = f.read()
            if current == contents:
                return True
            print("  file contents still mismatched")
            print("  wanted: {}".format(contents.replace('\n', ' ')))
            print("     got: {}".format(current.replace('\n', ' ')))
        time.sleep(1)
    if exists(path):
        raise Exception("Contents of '{}' mismatched after {}s".format(path, timeout))
    raise Exception("Didn't find '{}' after {}s".format(path, timeout))


def await_file_vanishes(path, timeout=10):
    start_time = time.time()
    while time.time() - start_time < timeout:
        print("  waiting for '{}' to vanish".format(path))
        if not exists(path):
            return
        time.sleep(1)
    raise Exception("'{}' still exists after {}s".format(path, timeout))


