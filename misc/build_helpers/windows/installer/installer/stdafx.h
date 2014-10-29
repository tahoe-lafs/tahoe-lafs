// stdafx.h : include file for standard system include files,
// or project specific include files that are used frequently, but
// are changed infrequently
//

#pragma once

#include "targetver.h"

#include <stdio.h>
#include <wchar.h>
#include <stdlib.h>
#include <errno.h>
#include <io.h>
#include <fcntl.h>
#include <process.h>
#include <sys/stat.h>

// Turn off the warnings nagging you to use the more complicated *_s
// "secure" functions that are actually more difficult to use securely.
#pragma warning(disable:4996)

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wtypes.h>
#include <objbase.h>
#include <shldisp.h>
#include <shellapi.h>
