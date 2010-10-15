=========================
Filesystem-specific notes
=========================

1. ext3_

Tahoe storage servers use a large number of subdirectories to store their
shares on local disk. This format is simple and robust, but depends upon the
local filesystem to provide fast access to those directories.

ext3
====

For moderate- or large-sized storage servers, you'll want to make sure the
"directory index" feature is enabled on your ext3 directories, otherwise
share lookup may be very slow. Recent versions of ext3 enable this
automatically, but older filesystems may not have it enabled::

  $ sudo tune2fs -l /dev/sda1 |grep feature
  Filesystem features:      has_journal ext_attr resize_inode dir_index filetype needs_recovery sparse_super large_file

If "dir_index" is present in the "features:" line, then you're all set. If
not, you'll need to use tune2fs and e2fsck to enable and build the index. See
<http://wiki.dovecot.org/MailboxFormat/Maildir> for some hints.
