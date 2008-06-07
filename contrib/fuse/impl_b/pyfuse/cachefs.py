import os, stat, py, select
import inspect
from objectfs import ObjectFs


BLOCKSIZE = 8192


def remote_runner(BLOCKSIZE):
    import sys, select, os, struct
    stream = None
    while True:
        while stream is not None:
            iwtd, owtd, ewtd = select.select([0], [1], [])
            if iwtd:
                break
            pos = stream.tell()
            data = stream.read(BLOCKSIZE)
            res = ('R', path, pos, len(data))
            sys.stdout.write('%r\n%s' % (res, data))
            if len(data) < BLOCKSIZE:
                stream = None

        stream = None
        msg = eval(sys.stdin.readline())
        if msg[0] == 'L':
            path = msg[1]
            names = os.listdir(path)
            res = []
            for name in names:
                try:
                    st = os.stat(os.path.join(path, name))
                except OSError:
                    continue
                res.append((name, st.st_mode, st.st_size))
            res = msg + (res,)
            sys.stdout.write('%s\n' % (res,))
        elif msg[0] == 'R':
            path, pos = msg[1:]
            f = open(path, 'rb')
            f.seek(pos)
            data = f.read(BLOCKSIZE)
            res = msg + (len(data),)
            sys.stdout.write('%r\n%s' % (res, data))
        elif msg[0] == 'S':
            path, pos = msg[1:]
            stream = open(path, 'rb')
            stream.seek(pos)
        #elif msg[0] == 'C':
        #    stream = None


class CacheFs(ObjectFs):
    MOUNT_OPTIONS = {'max_read': BLOCKSIZE}

    def __init__(self, localdir, remotehost, remotedir):
        src = inspect.getsource(remote_runner)
        src += '\n\nremote_runner(%d)\n' % BLOCKSIZE

        remotecmd = 'python -u -c "exec input()"'
        cmdline = [remotehost, remotecmd]
        # XXX Unix style quoting
        for i in range(len(cmdline)):
            cmdline[i] = "'" + cmdline[i].replace("'", "'\\''") + "'"
        cmd = 'ssh -C'
        cmdline.insert(0, cmd)

        child_in, child_out = os.popen2(' '.join(cmdline), bufsize=0)
        child_in.write('%r\n' % (src,))

        control = Controller(child_in, child_out)
        ObjectFs.__init__(self, CacheDir(localdir, remotedir, control))


class Controller:
    def __init__(self, child_in, child_out):
        self.child_in = child_in
        self.child_out = child_out
        self.cache = {}
        self.streaming = None

    def next_answer(self):
        answer = eval(self.child_out.readline())
        #print 'A', answer
        if answer[0] == 'R':
            remotefn, pos, length = answer[1:]
            data = self.child_out.read(length)
            self.cache[remotefn, pos] = data
        return answer

    def wait_answer(self, query):
        self.streaming = None
        #print 'Q', query
        self.child_in.write('%r\n' % (query,))
        while True:
            answer = self.next_answer()
            if answer[:len(query)] == query:
                return answer[len(query):]

    def listdir(self, remotedir):
        query = ('L', remotedir)
        res, = self.wait_answer(query)
        return res

    def wait_for_block(self, remotefn, pos):
        key = remotefn, pos
        while key not in self.cache:
            self.next_answer()
        return self.cache[key]

    def peek_for_block(self, remotefn, pos):
        key = remotefn, pos
        while key not in self.cache:
            iwtd, owtd, ewtd = select.select([self.child_out], [], [], 0)
            if not iwtd:
                return None
            self.next_answer()
        return self.cache[key]

    def cached_block(self, remotefn, pos):
        key = remotefn, pos
        return self.cache.get(key)

    def start_streaming(self, remotefn, pos):
        if remotefn != self.streaming:
            while (remotefn, pos) in self.cache:
                pos += BLOCKSIZE
            query = ('S', remotefn, pos)
            #print 'Q', query
            self.child_in.write('%r\n' % (query,))
            self.streaming = remotefn

    def read_blocks(self, remotefn, poslist):
        lst = ['%r\n' % (('R', remotefn, pos),)
               for pos in poslist if (remotefn, pos) not in self.cache]
        if lst:
            self.streaming = None
            #print 'Q', '+ '.join(lst)
            self.child_in.write(''.join(lst))

    def clear_cache(self, remotefn):
        for key in self.cache.keys():
            if key[0] == remotefn:
                del self.cache[key]


class CacheDir:
    def __init__(self, localdir, remotedir, control, size=0):
        self.localdir  = localdir
        self.remotedir = remotedir
        self.control   = control
        self.entries   = None
    def listdir(self):
        if self.entries is None:
            self.entries = []
            for name, st_mode, st_size in self.control.listdir(self.remotedir):
                if stat.S_ISDIR(st_mode):
                    cls = CacheDir
                else:
                    cls = CacheFile
                obj = cls(os.path.join(self.localdir, name),
                          os.path.join(self.remotedir, name),
                          self.control,
                          st_size)
                self.entries.append((name, obj))
        return self.entries

class CacheFile:
    def __init__(self, localfn, remotefn, control, size):
        self.localfn  = localfn
        self.remotefn = remotefn
        self.control  = control
        self.st_size  = size

    def size(self):
        return self.st_size

    def read(self):
        try:
            st = os.stat(self.localfn)
        except OSError:
            pass
        else:
            if st.st_size == self.st_size:     # fully cached
                return open(self.localfn, 'rb')
            os.unlink(self.localfn)
        lpath = py.path.local(self.partial())
        lpath.ensure(file=1)
        f = open(self.partial(), 'r+b')
        return DumpFile(self, f)

    def partial(self):
        return self.localfn + '.partial~'

    def complete(self):
        try:
            os.rename(self.partial(), self.localfn)
        except OSError:
            pass


class DumpFile:

    def __init__(self, cf, f):
        self.cf = cf
        self.f = f
        self.pos = 0

    def seek(self, npos):
        self.pos = npos

    def read(self, count):
        control = self.cf.control
        self.f.seek(self.pos)
        buffer = self.f.read(count)
        self.pos += len(buffer)
        count -= len(buffer)

        self.f.seek(0, 2)
        curend = self.f.tell()

        if count > 0:

            while self.pos > curend:
                curend &= -BLOCKSIZE
                data = control.peek_for_block(self.cf.remotefn, curend)
                if data is None:
                    break
                self.f.seek(curend)
                self.f.write(data)
                curend += len(data)
                if len(data) < BLOCKSIZE:
                    break

            start = max(self.pos, curend) & (-BLOCKSIZE)
            end = (self.pos + count + BLOCKSIZE-1) & (-BLOCKSIZE)
            poslist = range(start, end, BLOCKSIZE)

            if self.pos <= curend:
                control.start_streaming(self.cf.remotefn, start)
                self.f.seek(start)
                for p in poslist:
                    data = control.wait_for_block(self.cf.remotefn, p)
                    assert self.f.tell() == p
                    self.f.write(data)
                    if len(data) < BLOCKSIZE:
                        break

                curend = self.f.tell()
                while curend < self.cf.st_size:
                    curend &= -BLOCKSIZE
                    data = control.cached_block(self.cf.remotefn, curend)
                    if data is None:
                        break
                    assert self.f.tell() == curend
                    self.f.write(data)
                    curend += len(data)
                else:
                    self.cf.complete()
                    control.clear_cache(self.cf.remotefn)

                self.f.seek(self.pos)
                buffer += self.f.read(count)

            else:
                control.read_blocks(self.cf.remotefn, poslist)
                result = []
                for p in poslist:
                    data = control.wait_for_block(self.cf.remotefn, p)
                    result.append(data)
                    if len(data) < BLOCKSIZE:
                        break
                data = ''.join(result)
                buffer += data[self.pos-start:self.pos-start+count]

        else:
            if self.pos + 60000 > curend:
                curend &= -BLOCKSIZE
                control.start_streaming(self.cf.remotefn, curend)

        return buffer
