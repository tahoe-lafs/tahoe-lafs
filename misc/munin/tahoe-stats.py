#!/usr/bin/python

import os
import pickle
import re
import sys

PLUGINS = {
    'tahoe_storage_consumed':
        { 'statid': 'storage_server.consumed',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Storage Server Space Consumed',
                                     'graph_vlabel bytes',
                                     'graph_category tahoe_storage_server',
                                     'graph_info This graph shows space consumed',
                                     'graph_args --base 1024',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },
    'tahoe_storage_allocated':
        { 'statid': 'storage_server.allocated',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Storage Server Space Allocated',
                                     'graph_vlabel bytes',
                                     'graph_category tahoe_storage_server',
                                     'graph_info This graph shows space allocated',
                                     'graph_args --base 1024',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },

    'tahoe_runtime_load_avg':
        { 'statid': 'load_monitor.avg_load',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Runtime Load Average',
                                     'graph_vlabel load',
                                     'graph_category tahoe',
                                     'graph_info This graph shows average reactor delay',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },
    'tahoe_runtime_load_peak':
        { 'statid': 'load_monitor.max_load',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Runtime Load Peak',
                                     'graph_vlabel load',
                                     'graph_category tahoe',
                                     'graph_info This graph shows peak reactor delay',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },

    'tahoe_storage_bytes_added':
        { 'statid': 'storage_server.bytes_added',
          'category': 'counters',
          'configheader': '\n'.join(['graph_title Tahoe Storage Server Bytes Added',
                                     'graph_vlabel bytes',
                                     'graph_category tahoe_storage_server',
                                     'graph_info This graph shows cummulative bytes added',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },
    'tahoe_storage_bytes_freed':
        { 'statid': 'storage_server.bytes_freed',
          'category': 'counters',
          'configheader': '\n'.join(['graph_title Tahoe Storage Server Bytes Removed',
                                     'graph_vlabel bytes',
                                     'graph_category tahoe_storage_server',
                                     'graph_info This graph shows cummulative bytes removed',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },

    }

def smash_name(name):
    return re.sub('[^a-zA-Z0-9]', '_', name)

def open_stats(fname):
    f = open(fname, 'rb')
    stats = pickle.load(f)
    f.close()
    return stats

def main(argv):
    graph_name = os.path.basename(argv[0])
    if graph_name.endswith('.py'):
        graph_name = graph_name[:-3]

    plugin_conf = PLUGINS.get(graph_name)

    for k,v in os.environ.items():
        if k.startswith('statsfile'):
            stats_file = v
            break
    else:
        raise RuntimeError("No 'statsfile' env var found")

    stats = open_stats(stats_file)

    def output_nodes(output_section):
        for tubid, nodestats in stats.items():
            name = smash_name("%s_%s" % (nodestats['nickname'], tubid[:8]))
            #value = nodestats['stats'][plugin_conf['category']].get(plugin_conf['statid'])
            category = plugin_conf['category']
            statid = plugin_conf['statid']
            value = nodestats['stats'][category].get(statid)
            if value is not None:
                args = { 'name': name, 'value': value }
                print plugin_conf[output_section] % args

    if len(argv) > 1:
        if sys.argv[1] == 'config':
            print plugin_conf['configheader']
            output_nodes('graph_config')
            sys.exit(0)

    output_nodes('graph_render')

if __name__ == '__main__':
    main(sys.argv)
