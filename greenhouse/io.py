import contextlib
import errno
import fcntl
import os
import socket
import stat
import weakref
try:
    from cStringIO import StringIO
except ImportError: #pragma: no cover
    from StringIO import StringIO

from greenhouse import utils
from greenhouse._state import state


__all__ = ["Socket", "File", "monkeypatch", "unmonkeypatch", "pipe"]


_socket = socket.socket
_open = __builtins__['open']
_file = __builtins__['file']

SOCKET_CLOSED = set((errno.ECONNRESET, errno.ENOTCONN, errno.ESHUTDOWN))

def monkeypatch():
    """replace functions in the standard library socket module
    with their non-blocking greenhouse equivalents"""
    socket.socket = Socket
    __builtins__['open'] = __builtins__['file'] = File

def unmonkeypatch():
    "undo a call to monkeypatch()"
    socket.socket = _socket
    __builtins__['open'] = _open
    __builtins__['file'] = _file

#@utils._debugger
class Socket(object):
    def __init__(self, *args, **kwargs):
        # wrap a basic socket or build our own
        self._sock = kwargs.pop('fromsock', None)
        if isinstance(self._sock, Socket):
            self._sock = self._sock._sock
        if not self._sock:
            self._sock = _socket(*args, **kwargs)

        # copy over attributes
        self.family = self._sock.family
        self.type = self._sock.type
        self.proto = self._sock.proto
        self._fileno = self._sock.fileno()

        # make the underlying socket non-blocking
        self.setblocking(False)

        # create events
        self._readable = utils.Event()
        self._writable = utils.Event()

        # some more housekeeping
        self._timeout = None
        self._closed = False

        # make sure we have a state.poller
        if not hasattr(state, 'poller'):
            import greenhouse.poller #pragma: no cover

        # allow for lookup by fileno
        state.descriptormap[self._fileno].append(weakref.ref(self))

    def __del__(self):
        try:
            state.poller.unregister(self)
        except:
            pass

    @contextlib.contextmanager
    def _registered(self):
        try:
            state.poller.register(self)
        except (IOError, OSError), error: #pragma: no cover
            if error.args and error.args[0] in errno.errorcode:
                raise socket.error(*error.args)
            raise

        yield

        try:
            state.poller.unregister(self)
        except (IOError, OSError), error: #pragma: no cover
            if error.args and error.args[0] in errno.errorcode:
                raise socket.error(*error.args)
            raise

    def accept(self):
        with self._registered():
            while 1:
                try:
                    client, addr = self._sock.accept()
                except socket.error, err:
                    if err[0] in (errno.EAGAIN, errno.EWOULDBLOCK):
                        self._readable.wait()
                        continue
                    else:
                        raise #pragma: no cover
                return type(self)(fromsock=client), addr

    def bind(self, *args, **kwargs):
        return self._sock.bind(*args, **kwargs)

    def close(self):
        self._closed = True
        return self._sock.close()

    def connect(self, address):
        with self._registered():
            while True:
                err = self.connect_ex(address)
                if err in (errno.EINPROGRESS, errno.EALREADY,
                        errno.EWOULDBLOCK):
                    self._writable.wait()
                    continue
                if err not in (0, errno.EISCONN): #pragma: no cover
                    raise socket.error(err, errno.errorcode[err])
                return

    def connect_ex(self, address):
        return self._sock.connect_ex(address)

    def dup(self):
        return type(self)(fromsock=self._sock.dup())

    def fileno(self):
        return self._fileno

    def getpeername(self):
        return self._sock.getpeername()

    def getsockname(self):
        return self._sock.getsockname()

    def getsockopt(self, *args):
        return self._sock.getsockopt(*args)

    def gettimeout(self):
        return self._timeout

    def listen(self, backlog):
        return self._sock.listen(backlog)

    def makefile(self, mode='r', bufsize=None):
        return socket._fileobject(self)

    def recv(self, nbytes):
        with self._registered():
            while 1:
                if self._closed:
                    raise socket.error(errno.EBADF, "Bad file descriptor")
                try:
                    return self._sock.recv(nbytes)
                except socket.error, e:
                    if e[0] == errno.EWOULDBLOCK: #pragma: no cover
                        self._readable.wait()
                        continue
                    if e[0] in SOCKET_CLOSED:
                        self._closed = True
                        return ''
                    raise #pragma: no cover

    def recv_into(self, buffer, nbytes):
        with self._registered():
            self._readable.wait()
            return self._sock.recv_into(buffer, nbytes)

    def recvfrom(self, nbytes):
        with self._registered():
            self._readable.wait()
            return self._sock.recvfrom(nbytes)

    def recvfrom_into(self, buffer, nbytes):
        with self._registered():
            self._readable.wait()
            return self._sock.recvfrom_into(buffer, nbytes)

    def send(self, data):
        with self._registered():
            try:
                return self._sock.send(data)
            except socket.error, err: #pragma: no cover
                if err[0] in (errno.EWOULDBLOCK, errno.ENOTCONN):
                    return 0
                raise

    def sendall(self, data):
        sent = self.send(data)
        while sent < len(data): #pragma: no cover
            self._writable.wait()
            sent += self.send(data[sent:])

    def sendto(self, *args):
        try:
            return self._sock.sendto(*args)
        except socket.error, err: #pragma: no cover
            if err[0] in (errno.EWOULDBLOCK, errno.ENOTCONN):
                return 0
            raise

    def setblocking(self, flag):
        return self._sock.setblocking(flag)

    def setsockopt(self, level, option, value):
        return self._sock.setsockopt(level, option, value)

    def shutdown(self, flag):
        return self._sock.shutdown(flag)

    def settimeout(self, timeout):
        self._timeout = timeout

#@utils._debugger
class File(object):
    CHUNKSIZE = 8192
    NEWLINE = "\n"

    @staticmethod
    def _mode_to_flags(mode):
        flags = os.O_RDONLY | os.O_NONBLOCK # always non-blocking
        if (('w' in mode or 'a' in mode) and 'r' in mode) or '+' in mode:
            # both read and write
            flags |= os.O_RDWR
        elif 'w' in mode or 'a' in mode:
            # else just write
            flags |= os.O_WRONLY

        if 'a' in mode:
            # append-write mode
            flags |= os.O_APPEND

        return flags

    def _set_up_waiting(self):
        if not hasattr(state, 'poller'):
            import greenhouse.poller #pragma: no cover
        try:
            state.poller.register(self)

            # if we got here, poller.register worked, so set up event-based IO
            self._wait = self._wait_event
            self._readable = utils.Event()
            self._writable = utils.Event()
            state.descriptormap[self._fileno].append(weakref.ref(self))
        except IOError:
            self._wait = self._wait_yield

    def __init__(self, name, mode='rb'):
        self.mode = mode
        self._buf = StringIO()

        # translate mode into the proper open flags
        flags = self._mode_to_flags(mode)

        # if write or append mode and the file doesn't exist, create it
        if flags & (os.O_WRONLY | os.O_RDWR) and not os.path.exists(name):
            os.mknod(name, 0644, stat.S_IFREG)

        # open the file, get a descriptor
        self._fileno = os.open(name, flags)

        # try to drive the asyncronous waiting off of the polling interface,
        # but epoll doesn't seem to support filesystem descriptors, so fall
        # back to a waiting with a simple yield
        self._set_up_waiting()

    def _wait_event(self, reading): #pragma: no cover
        "wait on our events"
        if reading:
            self._readable.wait()
        else:
            self._writable.wait()

    def _wait_yield(self, reading): #pragma: no cover
        "generic wait, for when polling won't work"
        scheduler.pause()

    @staticmethod
    def _add_flags(fd, flags):
        fdflags = fcntl.fcntl(fd, fcntl.F_GETFL)
        if fdflags & flags != flags:
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | fdflags)

    @classmethod
    def fromfd(cls, fd, mode='rb'):
        fp = object.__new__(cls) # bypass __init__
        fp.mode = mode
        fp._fileno = fd
        fp._buf = StringIO()

        cls._add_flags(fd, cls._mode_to_flags(mode))
        fp._set_up_waiting()

        return fp

    def __iter__(self):
        line = self.readline()
        while line:
            yield line
            line = self.readline()

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.close()

    def __del__(self):
        try:
            state.poller.unregister(self)
        except:
            pass

    def close(self):
        os.close(self._fileno)
        state.poller.unregister(self)

    def fileno(self):
        return self._fileno

    def read(self, size=-1):
        chunksize = size < 0 and self.CHUNKSIZE or min(self.CHUNKSIZE, size)

        buf = self._buf
        buf.seek(0, os.SEEK_END)
        collected = buf.tell()

        while 1:
            if size >= 0 and collected >= size:
                # we have read enough already
                break

            try:
                output = os.read(self._fileno, chunksize)
            except (OSError, IOError), err: #pragma: no cover
                if err.args[0] in (errno.EAGAIN, errno.EINTR):
                    # would have blocked
                    self._wait(reading=True)
                    continue
                else:
                    raise

            if not output:
                # nothing more to read
                break

            collected += len(output)
            buf.write(output)

        # get rid of the old buffer
        rc = buf.getvalue()
        buf.seek(0)
        buf.truncate()

        if size >= 0:
            # leave the overflow in the buffer
            buf.write(rc[size:])
            return rc[:size]
        return rc

    def readline(self):
        buf = self._buf
        newline, chunksize = self.NEWLINE, self.CHUNKSIZE
        buf.seek(0)

        text = buf.read()
        while text.find(newline) < 0:
            try:
                text = os.read(self._fileno, chunksize)
            except (OSError, IOError), err: #pragma: no cover
                if err.args[0] in (errno.EAGAIN, errno.EINTR):
                    # would have blocked
                    self._wait(reading=True)
                    continue
                else:
                    raise
            if not text:
                break
            buf.write(text)
        else:
            # found a newline
            rc = buf.getvalue()
            index = rc.find(newline) + len(newline)

            buf.seek(0)
            buf.truncate()
            buf.write(rc[index:])
            return rc[:index]

        # hit the end of the file, no more newlines
        rc = buf.getvalue()
        buf.seek(0)
        buf.truncate()
        return rc

    def readlines(self):
        return list(self.__iter__())

    def seek(self, pos, modifier=0):
        os.lseek(self._fileno, pos, modifier)

        # clear out the buffer
        buf = self._buf
        buf.seek(0)
        buf.truncate()

    def tell(self):
        with os.fdopen(os.dup(self._fileno)) as fp:
            return fp.tell()

    def write(self, data):
        while data:
            try:
                went = os.write(self._fileno, data)
            except (OSError, IOError), err: #pragma: no cover
                if err.args[0] in (errno.EAGAIN, errno.EINTR):
                    self._wait(reading=False)
                    continue
                else:
                    raise

            data = data[went:]

    def writelines(self, lines):
        self.write("".join(lines))

def pipe():
    r, w = os.pipe()
    return File.fromfd(r, 'rb'), File.fromfd(w, 'wb')
