// installer.cpp : Defines the entry point for the console application.
//

#include "stdafx.h"

int wmain(int argc, wchar_t *argv[]);
wchar_t * get_default_destination_dir();
void self_extract(wchar_t *destination_dir);
void empty_directory(wchar_t *destination_dir);
void unzip_from_executable(wchar_t *executable_path, wchar_t *destination_dir);
size_t read_uint32_le(unsigned char *b);
void unzip(wchar_t *zip_path, wchar_t *destination_dir);
bool spawn_with_redirect(FILE *redirect, unsigned char *output_buf, size_t output_size, const wchar_t *argv[]);
void install_python(wchar_t *python_installer_dir);
void scriptsetup(wchar_t *destination_dir);
void pause();

#define fail_unless(x, s) if (!(x)) { fail(s); }
void fail(char *s);
void warn(char *s);

#define REQUIRED_PYTHON_VERSION_PREFIX "Python 2.7."

// defines PKGNAME_AND_VERSION
#include "_version.h"


void noop_handler(const wchar_t * expression,
                  const wchar_t * function,
                  const wchar_t * file,
                  unsigned int line,
                  uintptr_t pReserved) {
}

int wmain(int argc, wchar_t *argv[]) {
	_set_invalid_parameter_handler(noop_handler);

	if (argc >= 2 && wcscmp(argv[1], L"--help") == 0) {
		printf("installer <destination_dir>\n");
	}
	wchar_t *destination_dir = (argc >= 2) ? argv[1] : get_default_destination_dir();

	self_extract(destination_dir);
	install_python(destination_dir);
	scriptsetup(destination_dir);
	pause();

	return 0;
}

wchar_t * get_default_destination_dir() {
	// TODO: get Program Files directory from the registry
	return L"C:\\Program Files\\Tahoe-LAFS";
}

void self_extract(wchar_t *destination_dir) {
	wchar_t executable_path[MAX_PATH];

	HMODULE hModule = GetModuleHandle(NULL);
	fail_unless(hModule != NULL, "Could not get the module handle.");
	GetModuleFileNameW(hModule, executable_path, MAX_PATH); 
	fail_unless(GetLastError() == ERROR_SUCCESS, "Could not get the path of the current executable.");

	empty_directory(destination_dir);
	unzip_from_executable(executable_path, destination_dir);
}

void empty_directory(wchar_t *destination_dir) {
#if 0
	// Delete contents of destination_dir if it already exists.

	struct _stat buf;
	if (_wstat(destination_dir, &buf) == 0) {
		wchar_t destination_dir_dblnul[MAX_PATH+1];
		size_t len = wcslen(destination_dir);
		fail_unless(len < MAX_PATH, "Destination path is too long.");
		wcscpy(destination_dir_dblnul, destination_dir);
		destination_dir_dblnul[len+1] = L'\0';

		SHFILEOPSTRUCTW shell_file_op = {
			NULL,
			FO_DELETE,
			destination_dir_dblnul,
			NULL,
			FOF_SILENT | FOF_NOERRORUI | FOF_NOCONFIRMATION,
			FALSE,
			NULL,
			NULL
		};
		int res = SHFileOperationW(&shell_file_op);
		fail_unless(res == 0, "Could not delete existing contents of destination directory.");
	}
#endif
	// Create an empty directory at destination_dir.
	errno = 0;
	int res = _wmkdir(destination_dir);
	fail_unless((res == 0 && errno == 0) || errno == EEXIST, "Could not create destination directory.");
}

void unzip_from_executable(wchar_t *executable_path, wchar_t *destination_dir) {
	// shell32's zipped folder implementation is strict about the zip format and
	// does not support unzipping a self-extracting exe directly. So we copy the
	// original zip file that was appended to the exe to a temporary directory,
	// and use shell32 to unzip it from there. To get the length of the zip file,
	// we look at its "end of central directory record", which is documented at
	// <http://www.pkware.com/documents/casestudies/APPNOTE.TXT>.
	// For simplicity we only handle the case of a zip file that has no archive
	// comment, that does not use disk spanning, and that does not have a
	// "Zip64 end of central directory record".

	// APPNOTE.TXT section 4.3.16.
	const size_t sizeof_eocd = 22;
	unsigned char end_data[sizeof_eocd];
	unsigned char eocd_signature[] = {0x50, 0x4B, 0x05, 0x06};
	unsigned char comment_length[] = {0x00, 0x00};
	unsigned char disk_num[] = {0x00, 0x00};

	errno = 0;
	FILE *f = _wfopen(executable_path, L"rb");
	fail_unless(f != NULL && errno == 0 && ferror(f) == 0,
		        "Could not open executable file.");

	fseek(f, -(off_t) sizeof_eocd, SEEK_END);
	fail_unless(errno == 0 && ferror(f) == 0,
		        "Could not seek to end-of-central-directory record.");

	__int64 eocd_offset = _ftelli64(f);
	fail_unless(errno == 0 && ferror(f) == 0 && eocd_offset >= 0,
		        "Could not read position of end-of-central-directory record.");
	fail_unless(eocd_offset + sizeof_eocd <= 0xFFFFFFFFi64,
		        "Cannot read an executable file >= 4 GiB.");

	size_t n = fread(end_data, sizeof(end_data), 1, f);
	fail_unless(n == 1 && errno == 0 && ferror(f) == 0,
		        "Could not read end records.");

	fail_unless(memcmp(end_data + sizeof(end_data) - sizeof(comment_length),
		               comment_length, sizeof(comment_length)) == 0,
		        "Cannot read a zip file that has an archive comment.");

	fail_unless(memcmp(end_data, eocd_signature, sizeof(eocd_signature)) == 0,
		        "Could not find the end-of-central-directory signature.");

	fail_unless(memcmp(end_data + 4, disk_num, sizeof(disk_num)) == 0 &&
		        memcmp(end_data + 6, disk_num, sizeof(disk_num)) == 0,
		        "Cannot read a zipfile that spans disks.");

	size_t cd_length = read_uint32_le(end_data + 12);
	size_t cd_offset = read_uint32_le(end_data + 16);
	__int64 zip_length = (__int64) cd_offset + cd_length + sizeof_eocd;
	fail_unless(zip_length <= 0x7FFFFFFFi64,
	            "Cannot copy a zip file >= 2 GiB.");

	fseek(f, -(off_t) zip_length, SEEK_END);
	fail_unless(errno == 0 && ferror(f) == 0,
		        "Could not seek to start of embedded zip file.");

	const wchar_t tmp_filename[] = L"tahoe-lafs.zip"; // FIXME make this more unique.
	wchar_t tmp_path[MAX_PATH];
	DWORD len = GetTempPathW(MAX_PATH, tmp_path);
	fail_unless(len > 0, "Could not obtain temporary directory path.");
	fail_unless(len < MAX_PATH - wcslen(tmp_filename), "Temporary directory path is too long.");
	wcscpy(tmp_path + len, tmp_filename);

	errno = 0;
	FILE *tmp_file = _wfopen(tmp_path, L"wb");
	fail_unless(tmp_file != NULL && errno == 0 && ferror(f) == 0,
		        "Could not open temporary zip file.");

	// FIXME: delete the temporary file if there is an error.
	unsigned char buf[16384];
	size_t remaining_length = (size_t) zip_length;
	while (remaining_length > 0) {
		size_t chunk_length = min(remaining_length, sizeof(buf));
		n = fread(buf, chunk_length, 1, f);
		fail_unless(n == 1 && errno == 0 && ferror(f) == 0,
		            "Could not read from executable file.");
		fwrite(buf, chunk_length, 1, tmp_file);
		fail_unless(n == 1 && errno == 0 && ferror(f) == 0,
		            "Could not write to temporary file.");
		remaining_length -= chunk_length;
	}
	int res = fclose(tmp_file);
	fail_unless(res == 0, "Could not close temporary zip file.");
	fclose(f); // ignore errors

	unzip(tmp_path, destination_dir);
	_wunlink(tmp_path); // ignore errors
}

// read unsigned little-endian 32-bit integer
size_t read_uint32_le(unsigned char *b) {
	return ((size_t) b[0]      ) |
		   ((size_t) b[1] <<  8) |
		   ((size_t) b[2] << 16) |
		   ((size_t) b[3] << 24);
}

void unzip(wchar_t *zip_path, wchar_t *destination_dir) {
	// Based loosely on
	// <https://social.msdn.microsoft.com/Forums/vstudio/en-US/45668d18-2840-4887-87e1-4085201f4103/visual-c-to-unzip-a-zip-file-to-a-specific-directory?forum=vclanguage>.

	// CoInitializeEx: <http://msdn.microsoft.com/en-gb/library/windows/desktop/ms695279(v=vs.85).aspx>
	HRESULT res = CoInitializeEx(NULL, 0);
	fail_unless(res == S_OK || res == S_FALSE, "Could not initialize COM.");

	// SysAllocString: <http://msdn.microsoft.com/en-gb/library/windows/desktop/ms221458(v=vs.85).aspx>
	// BSTR: <http://msdn.microsoft.com/en-us/library/windows/desktop/ms221069(v=vs.85).aspx>

	VARIANT zip_path_var;
	zip_path_var.vt = VT_BSTR;
	zip_path_var.bstrVal = SysAllocString(zip_path);
	fail_unless(zip_path_var.bstrVal != NULL, "Could not allocate string for zip file path.");

	VARIANT destination_dir_var;
	destination_dir_var.vt = VT_BSTR;
	destination_dir_var.bstrVal = SysAllocString(destination_dir);
	fail_unless(destination_dir_var.bstrVal != NULL, "Could not allocate string for destination directory path.");

	// CoCreateInstance: <http://msdn.microsoft.com/en-gb/library/windows/desktop/ms686615(v=vs.85).aspx>
	IShellDispatch *shell;
	res = CoCreateInstance(CLSID_Shell, NULL, CLSCTX_INPROC_SERVER, IID_IShellDispatch, (void **) &shell);
	fail_unless(res == S_OK, "Could not create Shell instance.");

	// Folder.NameSpace: <http://msdn.microsoft.com/en-gb/library/windows/desktop/gg537721(v=vs.85).aspx>
	Folder *zip_folder = NULL;
	res = shell->NameSpace(zip_path_var, &zip_folder);
	fail_unless(res == S_OK && zip_folder != NULL, "Could not create zip Folder object.");

	Folder *destination_folder = NULL;
	res = shell->NameSpace(destination_dir_var, &destination_folder);
	fail_unless(res == S_OK && destination_folder != NULL, "Could not create destination Folder object.");

	FolderItems *zip_folderitems = NULL;
	zip_folder->Items(&zip_folderitems);
	fail_unless(zip_folderitems != NULL, "Could not create zip FolderItems object.");

	VARIANT zip_idispatch_var;
	zip_idispatch_var.vt = VT_DISPATCH;
	zip_idispatch_var.pdispVal = NULL;
	zip_folderitems->QueryInterface(IID_IDispatch, (void **) &zip_idispatch_var.pdispVal);
	fail_unless(zip_idispatch_var.pdispVal != NULL, "Could not create IDispatch for zip FolderItems object.");

	// Folder.CopyHere: <http://msdn.microsoft.com/en-us/library/ms723207(v=vs.85).aspx>
	//   (16) Respond with "Yes to All" for any dialog box that is displayed.
	//  (256) Display a progress dialog box but do not show the file names.
	//  (512) Do not confirm the creation of a new directory if the operation requires one to be created.
	// (1024) Do not display a user interface if an error occurs.
	// These options are ignored on Windows XP.
	VARIANT options_var;
	options_var.vt = VT_I4;
	options_var.lVal = 16 | 256 | 512 | 1024;

	res = destination_folder->CopyHere(zip_idispatch_var, options_var);
	fail_unless(res == S_OK, "Could not extract zip file contents to destination directory.");

	// We don't bother to free/release stuff unless we succeed, since we exit on failure.

	// SysFreeString: <http://msdn.microsoft.com/en-gb/library/windows/desktop/ms221481(v=vs.85).aspx>
	SysFreeString(zip_path_var.bstrVal);
	SysFreeString(destination_dir_var.bstrVal);
	zip_idispatch_var.pdispVal->Release();
	zip_folderitems->Release();
	destination_folder->Release();
	zip_folder->Release();
	shell->Release();

	// CoUninitialize: <http://msdn.microsoft.com/en-gb/library/windows/desktop/ms688715(v=vs.85).aspx>
	CoUninitialize();
}

bool spawn_with_redirect(FILE *redirect, unsigned char *output_buf, size_t output_size, const wchar_t *argv[]) {
	bool result = false;
	fail_unless(output_size > 0, "Invalid output_size.");
	output_buf[0] = 0;

	// Redirection is annoyingly complicated.
	int output_pipe[2];
	errno = 0;
	int res = _pipe(output_pipe, 512, _O_BINARY | _O_NOINHERIT);
	if (res != 0) {
		warn("Could not create pipe.");
		return false;
	}
	int output_read_fd = output_pipe[0], output_write_fd = output_pipe[1];

	// Duplicate the redirected file descriptor (the call to _dup2 will close the original).
	int original_fd = _dup(_fileno(redirect));
	if (errno != 0) {
		warn("Could not duplicate original file descriptor.");
		return false;
	}

	// Duplicate write end of pipe to redirected file descriptor.
	res = _dup2(output_write_fd, _fileno(redirect));
	if (res != 0 || errno != 0) {
		warn("Could not redirect.");
		return false;
	}

	// Close original file descriptor for write end of pipe.
	_close(output_write_fd); // ignore errors

	HANDLE process_handle = (HANDLE) _wspawnvp(P_NOWAIT, argv[0], argv);
	if (process_handle == (HANDLE) -1) {
		warn("Could not execute subprocess.");
	}

	// Duplicate copy of original stdout back into stdout.
	errno = 0;
	res = _dup2(original_fd, _fileno(redirect));
	fail_unless(res == 0 && errno == 0, "Could not restore stdout.");

	// Close duplicate copy of original fd.
	_close(original_fd); // ignore errors

	if (process_handle == (HANDLE) -1) {
		return false;
	}

	DWORD exit_code = 0;
	errno = 0;
	unsigned char *p = output_buf;
	size_t remaining_size = output_size;
	int bytes_read;
	do {
		if (remaining_size == 0) {
			bytes_read = 0;
			Sleep(100);
		} else {
			bytes_read = _read(output_read_fd, p, remaining_size-1);
			if (errno != 0 || bytes_read < 0) {
				warn("Could not read from subprocess output.");
				return false;
			}
			fail_unless((size_t) bytes_read < output_size, "Unexpectedly long read.");
			p += bytes_read;
			remaining_size -= bytes_read;
			*p = 0;
		}

		// GetExitCodeProcess: <http://msdn.microsoft.com/en-gb/library/windows/desktop/ms683189(v=vs.85).aspx>
		BOOL res = GetExitCodeProcess(process_handle, &exit_code);
		if (!res) {
			warn("Could not get subprocess exit code.");
			return false;
		}
	} while (bytes_read > 0 && exit_code == STILL_ACTIVE);

	return (exit_code == 0);
}

void install_python(wchar_t *python_installer_dir) {
	printf("Checking for " REQUIRED_PYTHON_VERSION_PREFIX "..\n");

	unsigned char output_buf[1024];
	const wchar_t *argv[] = { L"python", L"-V", NULL };
	bool res = spawn_with_redirect(stderr, output_buf, sizeof(output_buf), &argv[0]);
	if (res) {
		printf("Found %s", (char *) output_buf);
		if (strncmp((char *) output_buf, REQUIRED_PYTHON_VERSION_PREFIX, strlen(REQUIRED_PYTHON_VERSION_PREFIX)) == 0) {
			return;
		} else {
			printf("but we need a newer version.\n");
		}
	} else {
		printf("No Python found.\n");
	}

	wchar_t installer_wildcard[] = L"\\python*.msi";
	if (python_installer_dir[wcslen(python_installer_dir)-1] == '\\') {
		wcscpy(installer_wildcard, L"*.msi");
	}
	wchar_t installer_pattern[MAX_PATH];
	fail_unless(wcslen(python_installer_dir) < MAX_PATH - wcslen(installer_wildcard),
	            "Could not construct pattern for Python installer.")
	wcscpy(installer_pattern, python_installer_dir);
	wcscat(installer_pattern, installer_wildcard);

	WIN32_FIND_DATA find_data;
	HANDLE search_handle = FindFirstFileW(installer_pattern, &find_data);
	fail_unless(search_handle != INVALID_HANDLE_VALUE,
	            "Could not find the Python installer.")

	fail_unless(wcslen(python_installer_dir) < MAX_PATH - wcslen(find_data.cFileName),
	            "Could not construct path to Python installer.")

	wchar_t installer_path[MAX_PATH];
	wcscpy(installer_path, python_installer_dir);
	wcscat(installer_path, find_data.cFileName);

	// <https://www.python.org/download/releases/2.5/msi/>
	// "/qb!" works, but it may silently remove a previous Python installation
	// that was not detected by the check above, and we want that to prompt.
	const wchar_t *python_installer_argv[] = {
		L"msiexec", L"/i", installer_path,
		// L"/qb!",
		L"ALLUSERS=1", L"ADDLOCAL=Extensions", NULL
	};
	errno = 0;
	intptr_t exit_code = _wspawnvp(P_WAIT, python_installer_argv[0], python_installer_argv);
	fail_unless(errno == 0, "Could not execute Python installer.");
	fail_unless(exit_code == 0, "Python installer failed.");
}

void scriptsetup(wchar_t *destination_dir) {
	wchar_t bin_dir[MAX_PATH];
	int n = wsnprintf(bin_dir, L"%ls\\%ls\\bin", destination_dir, PKGNAME_AND_VERSION);
	fail_unless(n >= 0 && n < MAX_PATH, "Could not construct path for bin directory.");

	unsigned char output_buf[10240];
	const wchar_t *scriptsetup_argv[] = {
		L"python", L"setup.py", L"scriptsetup",
		L"--allusers", L"--addpaths", bin_dir,
		NULL
	};
	bool res = spawn_with_redirect(stdout, output_buf, sizeof(output_buf), &scriptsetup_argv[0]);
	puts((char *) output_buf);
	fail_unless(res, "Could not set up Python to run the 'tahoe' command.");
}

void fail(char *s) {
	// TODO: show dialog box
	fprintf(stderr, "%s\n", s);
	pause()
	exit(1);
}

void warn(char *s) {
	fprintf(stderr, "%s\n", s);
}

void pause() {
	printf("Press any key to finish.");
	char buf[2];
	fgets(buf, 1, stdin);
}