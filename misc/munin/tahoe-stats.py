#!/usr/bin/python

import os
import pickle
import re
import sys
import time

STAT_VALIDITY = 300 # 5min limit on reporting stats

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

    'tahoe_helper_incoming_files':
        { 'statid': 'chk_upload_helper.incoming_count',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Upload Helper Incoming File Count',
                                     'graph_vlabel n files',
                                     'graph_category tahoe_helper',
                                     'graph_info This graph shows number of incoming files',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },
    'tahoe_helper_incoming_filesize':
        { 'statid': 'chk_upload_helper.incoming_size',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Upload Helper Incoming File Size',
                                     'graph_vlabel bytes',
                                     'graph_category tahoe_helper',
                                     'graph_info This graph shows total size of incoming files',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },
    'tahoe_helper_incoming_files_old':
        { 'statid': 'chk_upload_helper.incoming_size_old',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Upload Helper Incoming Old Files',
                                     'graph_vlabel bytes',
                                     'graph_category tahoe_helper',
                                     'graph_info This graph shows total size of old incoming files',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },

    'tahoe_helper_encoding_files':
        { 'statid': 'chk_upload_helper.encoding_count',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Upload Helper Encoding File Count',
                                     'graph_vlabel n files',
                                     'graph_category tahoe_helper',
                                     'graph_info This graph shows number of encoding files',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },
    'tahoe_helper_encoding_filesize':
        { 'statid': 'chk_upload_helper.encoding_size',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Upload Helper Encoding File Size',
                                     'graph_vlabel bytes',
                                     'graph_category tahoe_helper',
                                     'graph_info This graph shows total size of encoding files',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },
    'tahoe_helper_encoding_files_old':
        { 'statid': 'chk_upload_helper.encoding_size_old',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Upload Helper Encoding Old Files',
                                     'graph_vlabel bytes',
                                     'graph_category tahoe_helper',
                                     'graph_info This graph shows total size of old encoding files',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },

    'tahoe_helper_active_uploads':
        { 'statid': 'chk_upload_helper.active_uploads',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Upload Helper Active Files',
                                     'graph_vlabel n files',
                                     'graph_category tahoe_helper',
                                     'graph_info This graph shows number of files actively being processed by the helper',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },

    'tahoe_helper_upload_requests':
        { 'statid': 'chk_upload_helper.upload_requests',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Upload Helper Upload Requests',
                                     'graph_vlabel requests',
                                     'graph_category tahoe_helper',
                                     'graph_info This graph shows the number of upload requests arriving at the helper',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },
    'tahoe_helper_upload_already_present':
        { 'statid': 'chk_upload_helper.upload_already_present',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Upload Helper Uploads Already Present',
                                     'graph_vlabel requests',
                                     'graph_category tahoe_helper',
                                     'graph_info This graph shows the number of uploads whose files are already present in the grid',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },
    'tahoe_helper_upload_need_upload':
        { 'statid': 'chk_upload_helper.upload_need_upload',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Upload Helper Uploads Needing Upload',
                                     'graph_vlabel requests',
                                     'graph_category tahoe_helper',
                                     'graph_info This graph shows the number of uploads whose files are not already present in the grid',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },
    'tahoe_helper_encoded_bytes':
        { 'statid': 'chk_upload_helper.encoded_bytes',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Upload Helper Encoded Bytes',
                                     'graph_vlabel bytes',
                                     'graph_category tahoe_helper',
                                     'graph_info This graph shows the number of bytes encoded by the helper',
                                     ]),
          'graph_config': '\n'.join(['%(name)s.label %(name)s',
                                     '%(name)s.draw LINE1',
                                     ]),
          'graph_render': '\n'.join(['%(name)s.value %(value)s',
                                     ]),
        },
    'tahoe_helper_fetched_bytes':
        { 'statid': 'chk_upload_helper.fetched_bytes',
          'category': 'stats',
          'configheader': '\n'.join(['graph_title Tahoe Upload Helper Fetched Bytes',
                                     'graph_vlabel bytes',
                                     'graph_category tahoe_helper',
                                     'graph_info This graph shows the number of bytes fetched by the helper',
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

    now = time.time()
    def output_nodes(output_section, check_time):
        for tubid, nodestats in stats.items():
            if check_time and (now - nodestats.get('timestamp', 0)) > STAT_VALIDITY:
                continue
            name = smash_name("%s_%s" % (nodestats['nickname'], tubid[:4]))
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
            output_nodes('graph_config', False)
            sys.exit(0)

    output_nodes('graph_render', True)

if __name__ == '__main__':
    main(sys.argv)
