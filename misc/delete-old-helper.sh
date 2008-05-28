#!/bin/sh

find helper/CHK_encoding -mtime +7 -print0 |xargs -0 rm
find helper/CHK_incoming -mtime +14 -print0 |xargs -0 rm
