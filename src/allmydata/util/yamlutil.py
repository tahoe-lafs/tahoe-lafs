import yaml

# Announcements contain unicode, because they come from JSON. We tell PyYAML
# to give us unicode instead of str/bytes.
def construct_unicode(loader, node):
    return node.value
yaml.SafeLoader.add_constructor("tag:yaml.org,2002:str",
                                construct_unicode)

def safe_load(f):
    return yaml.safe_load(f)

def safe_dump(obj):
    return yaml.safe_dump(obj)
