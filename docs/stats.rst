.. -*- coding: utf-8-with-signature -*-

================
Tahoe Statistics
================

1. `Overview`_
2. `Statistics Categories`_
3. `Using Munin To Graph Stats Values`_

Overview
========

Each Tahoe node collects and publishes statistics about its operations as it
runs. These include counters of how many files have been uploaded and
downloaded, CPU usage information, performance numbers like latency of
storage server operations, and available disk space.

The easiest way to see the stats for any given node is use the web interface.
From the main "Welcome Page", follow the "Operational Statistics" link inside
the small "This Client" box. If the welcome page lives at
http://localhost:3456/, then the statistics page will live at
http://localhost:3456/statistics . This presents a summary of the stats
block, along with a copy of the raw counters. To obtain just the raw counters
(in JSON format), use /statistics?t=json instead.

Statistics Categories
=====================

The stats dictionary contains two keys: 'counters' and 'stats'. 'counters'
are strictly counters: they are reset to zero when the node is started, and
grow upwards. 'stats' are non-incrementing values, used to measure the
current state of various systems. Some stats are actually booleans, expressed
as '1' for true and '0' for false (internal restrictions require all stats
values to be numbers).

Under both the 'counters' and 'stats' dictionaries, each individual stat has
a key with a dot-separated name, breaking them up into groups like
'cpu_monitor' and 'storage_server'.

The currently available stats (as of release 1.6.0 or so) are described here:

**counters.storage_server.\***

    this group counts inbound storage-server operations. They are not provided
    by client-only nodes which have been configured to not run a storage server
    (with [storage]enabled=false in tahoe.cfg)

    allocate, write, close, abort
        these are for immutable file uploads. 'allocate' is incremented when a
        client asks if it can upload a share to the server. 'write' is
        incremented for each chunk of data written. 'close' is incremented when
        the share is finished. 'abort' is incremented if the client abandons
        the upload.

    get, read
        these are for immutable file downloads. 'get' is incremented
        when a client asks if the server has a specific share. 'read' is
        incremented for each chunk of data read.

    readv, writev
        these are for immutable file creation, publish, and retrieve. 'readv'
        is incremented each time a client reads part of a mutable share.
        'writev' is incremented each time a client sends a modification
        request.

    add-lease, renew, cancel
        these are for share lease modifications. 'add-lease' is incremented
        when an 'add-lease' operation is performed (which either adds a new
        lease or renews an existing lease). 'renew' is for the 'renew-lease'
        operation (which can only be used to renew an existing one). 'cancel'
        is used for the 'cancel-lease' operation.

    bytes_freed
        this counts how many bytes were freed when a 'cancel-lease'
        operation removed the last lease from a share and the share
        was thus deleted.

    bytes_added
        this counts how many bytes were consumed by immutable share
        uploads. It is incremented at the same time as the 'close'
        counter.

**stats.storage_server.\***

    allocated
        this counts how many bytes are currently 'allocated', which
        tracks the space that will eventually be consumed by immutable
        share upload operations. The stat is increased as soon as the
        upload begins (at the same time the 'allocated' counter is
        incremented), and goes back to zero when the 'close' or 'abort'
        message is received (at which point the 'disk_used' stat should
        incremented by the same amount).

    disk_total, disk_used, disk_free_for_root, disk_free_for_nonroot, disk_avail, reserved_space
        these all reflect disk-space usage policies and status.
        'disk_total' is the total size of disk where the storage
        server's BASEDIR/storage/shares directory lives, as reported
        by /bin/df or equivalent. 'disk_used', 'disk_free_for_root',
        and 'disk_free_for_nonroot' show related information.
        'reserved_space' reports the reservation configured by the
        tahoe.cfg [storage]reserved_space value. 'disk_avail'
        reports the remaining disk space available for the Tahoe
        server after subtracting reserved_space from disk_avail. All
        values are in bytes.

    accepting_immutable_shares
        this is '1' if the storage server is currently accepting uploads of
        immutable shares. It may be '0' if a server is disabled by
        configuration, or if the disk is full (i.e. disk_avail is less than
        reserved_space).

    total_bucket_count
        this counts the number of 'buckets' (i.e. unique
        storage-index values) currently managed by the storage
        server. It indicates roughly how many files are managed
        by the server.

    latencies.*.*
        these stats keep track of local disk latencies for
        storage-server operations. A number of percentile values are
        tracked for many operations. For example,
        'storage_server.latencies.readv.50_0_percentile' records the
        median response time for a 'readv' request. All values are in
        seconds. These are recorded by the storage server, starting
        from the time the request arrives (post-deserialization) and
        ending when the response begins serialization. As such, they
        are mostly useful for measuring disk speeds. The operations
        tracked are the same as the counters.storage_server.* counter
        values (allocate, write, close, get, read, add-lease, renew,
        cancel, readv, writev). The percentile values tracked are:
        mean, 01_0_percentile, 10_0_percentile, 50_0_percentile,
        90_0_percentile, 95_0_percentile, 99_0_percentile,
        99_9_percentile. (the last value, 99.9 percentile, means that
        999 out of the last 1000 operations were faster than the
        given number, and is the same threshold used by Amazon's
        internal SLA, according to the Dynamo paper).
        Percentiles are only reported in the case of a sufficient
        number of observations for unambiguous interpretation. For
        example, the 99.9th percentile is (at the level of thousandths
        precision) 9 thousandths greater than the 99th
        percentile for sample sizes greater than or equal to 1000,
        thus the 99.9th percentile is only reported for samples of 1000
        or more observations.


**counters.uploader.files_uploaded**

**counters.uploader.bytes_uploaded**

**counters.downloader.files_downloaded**

**counters.downloader.bytes_downloaded**

    These count client activity: a Tahoe client will increment these when it
    uploads or downloads an immutable file. 'files_uploaded' is incremented by
    one for each operation, while 'bytes_uploaded' is incremented by the size of
    the file.

**counters.mutable.files_published**

**counters.mutable.bytes_published**

**counters.mutable.files_retrieved**

**counters.mutable.bytes_retrieved**

 These count client activity for mutable files. 'published' is the act of
 changing an existing mutable file (or creating a brand-new mutable file).
 'retrieved' is the act of reading its current contents.

**counters.chk_upload_helper.\***

    These count activity of the "Helper", which receives ciphertext from clients
    and performs erasure-coding and share upload for files that are not already
    in the grid. The code which implements these counters is in
    src/allmydata/immutable/offloaded.py .

    upload_requests
        incremented each time a client asks to upload a file
        upload_already_present: incremented when the file is already in the grid

    upload_need_upload
        incremented when the file is not already in the grid

    resumes
        incremented when the helper already has partial ciphertext for
        the requested upload, indicating that the client is resuming an
        earlier upload

    fetched_bytes
        this counts how many bytes of ciphertext have been fetched
        from uploading clients

    encoded_bytes
        this counts how many bytes of ciphertext have been
        encoded and turned into successfully-uploaded shares. If no
        uploads have failed or been abandoned, encoded_bytes should
        eventually equal fetched_bytes.

**stats.chk_upload_helper.\***

    These also track Helper activity:

    active_uploads
        how many files are currently being uploaded. 0 when idle.

    incoming_count
        how many cache files are present in the incoming/ directory,
        which holds ciphertext files that are still being fetched
        from the client

    incoming_size
        total size of cache files in the incoming/ directory

    incoming_size_old
        total size of 'old' cache files (more than 48 hours)

    encoding_count
        how many cache files are present in the encoding/ directory,
        which holds ciphertext files that are being encoded and
        uploaded

    encoding_size
        total size of cache files in the encoding/ directory

    encoding_size_old
        total size of 'old' cache files (more than 48 hours)

**stats.node.uptime**
    how many seconds since the node process was started

**stats.cpu_monitor.\***

    1min_avg, 5min_avg, 15min_avg
        estimate of what percentage of system CPU time was consumed by the
        node process, over the given time interval. Expressed as a float, 0.0
        for 0%, 1.0 for 100%

    total
        estimate of total number of CPU seconds consumed by node since
        the process was started. Ticket #472 indicates that .total may
        sometimes be negative due to wraparound of the kernel's counter.


Using Munin To Graph Stats Values
=================================

The misc/operations_helpers/munin/ directory contains various plugins to
graph stats for Tahoe nodes. They are intended for use with the Munin_
system-management tool, which typically polls target systems every 5 minutes
and produces a web page with graphs of various things over multiple time
scales (last hour, last month, last year).

Most of the plugins are designed to pull stats from a single Tahoe node, and
are configured with the e.g. http://localhost:3456/statistics?t=json URL. The
"tahoe_stats" plugin is designed to read from the JSON file created by the
stats-gatherer. Some plugins are to be used with the disk watcher, and a few
(like tahoe_nodememory) are designed to watch the node processes directly
(and must therefore run on the same host as the target node).

Please see the docstrings at the beginning of each plugin for details, and
the "tahoe-conf" file for notes about configuration and installing these
plugins into a Munin environment.

.. _Munin: http://munin-monitoring.org/


Scraping Stats Values in OpenMetrics Format
===========================================

Time Series DataBase (TSDB) software like Prometheus_ and VictoriaMetrics_ can
parse statistics from the e.g. http://localhost:3456/statistics?t=openmetrics
URL in OpenMetrics_ format. Software like Grafana_ can then be used to graph
and alert on these numbers. You can find a pre-configured dashboard for
Grafana at https://grafana.com/grafana/dashboards/16894-tahoe-lafs/.

.. _OpenMetrics: https://openmetrics.io/
.. _Prometheus: https://prometheus.io/
.. _VictoriaMetrics: https://victoriametrics.com/
.. _Grafana: https://grafana.com/
