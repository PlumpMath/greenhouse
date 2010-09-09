import collections
import errno
import select

from greenhouse.scheduler import state


__all__ = ["Epoll", "Poll", "Select", "best", "set"]


class Poll(object):
    "a greenhouse poller using the poll system call''"
    INMASK = getattr(select, 'POLLIN', 0)
    OUTMASK = getattr(select, 'POLLOUT', 0)
    ERRMASK = getattr(select, 'POLLERR', 0) | getattr(select, "POLLHUP", 0)

    _POLLER = getattr(select, "poll", None)

    def __init__(self):
        self._poller = self._POLLER()
        self._registry = collections.defaultdict(list)

    def _register(self, fd, mask):
        return self._poller.register(fd, mask)

    def _unregister(self, fd):
        self._poller.unregister(fd)

    def register(self, fd, eventmask=None):
        # integer file descriptor
        fd = isinstance(fd, int) and fd or fd.fileno()

        # mask nothing by default
        if eventmask is None:
            eventmask = self.INMASK | self.OUTMASK | self.ERRMASK

        # get the mask of the current registration, if any
        registered = self._registry.get(fd)
        registered = registered and registered[-1] or 0

        # make sure eventmask includes all previous masks
        newmask = eventmask | registered

        # unregister the old mask
        if registered:
            self._unregister(fd)

        # register the new mask
        rc = self._register(fd, newmask)

        # append to a list of eventmasks so we can backtrack
        self._registry[fd].append(newmask)

        return rc

    def unregister(self, fd):
        # integer file descriptor
        fd = isinstance(fd, int) and fd or fd.fileno()

        # allow for extra noop calls
        if not self._registry.get(fd):
            return

        # unregister the current registration
        self._unregister(fd)
        self._registry[fd].pop()

        # re-do the previous registration, if any
        newmask = self._registry[fd]
        newmask = newmask and newmask[-1]
        if newmask:
            self._register(fd, newmask)
        else:
            self._registry.pop(fd)

    def poll(self, timeout):
        return self._poller.poll(timeout)


class Epoll(Poll):
    "a greenhouse poller utilizing the 2.6+ stdlib's epoll support"
    INMASK = getattr(select, 'EPOLLIN', 0)
    OUTMASK = getattr(select, 'EPOLLOUT', 0)
    ERRMASK = getattr(select, 'EPOLLERR', 0) | getattr(select, "EPOLLHUP", 0)

    _POLLER = getattr(select, "epoll", None)


class KQueue(Poll):
    "a greenhouse poller using the 2.6+ stdlib's kqueue support"
    INMASK = 1
    OUTMASK = 2
    ERRMASK = 0

    _POLLER = getattr(select, "kqueue", None)

    _mask_map = {
        getattr(select, "KQ_FILTER_READ", 0): INMASK,
        getattr(select, "KQ_FILTER_WRITE", 0): OUTMASK,
    }

    def _register(self, fd, mask):
        evs = []
        if mask & self.INMASK:
            evs.append(select.kevent(
                fd, select.KQ_FILTER_READ, select.KQ_EV_ADD))
        if mask & self.OUTMASK:
            evs.append(select.kevent(
                fd, select.KQ_FILTER_WRITE, select.KQ_EV_ADD))
        self._poller.control(evs, 0)

    def _unregister(self, fd):
        try:
            self._poller.control([
                    select.kevent(
                        fd, select.KQ_FILTER_READ, select.KQ_EV_DELETE),
                    select.kevent(
                        fd, select.KQ_FILTER_WRITE, select.KQ_EV_DELETE)],
                0)
        except EnvironmentError, err:
            if err.args[0] != errno.ENOENT:
                raise

    def poll(self, timeout):
        evs = self._poller.control(None, 2 * len(self._registry), timeout)
        return [(ev.ident, self._mask_map[ev.filter]) for ev in evs]


class Select(object):
    "a greenhouse poller using the select system call"
    INMASK = 1
    OUTMASK = 2
    ERRMASK = 4

    def __init__(self):
        self._registry = collections.defaultdict(list)
        self._currentmasks = {}

    def register(self, fd, eventmask=None):
        # integer file descriptor
        fd = isinstance(fd, int) and fd or fd.fileno()

        # mask nothing by default
        if eventmask is None:
            eventmask = self.INMASK | self.OUTMASK | self.ERRMASK

        # get the mask of the current registration, if any
        registered = self._registry.get(fd)
        registered = registered and registered[-1] or 0

        # make sure eventmask includes all previous masks
        newmask = eventmask | registered

        # apply the new mask
        self._currentmasks[fd] = newmask

        # append to the list of masks so we can backtrack
        self._registry[fd].append(newmask)

        return not registered

    def unregister(self, fd):
        # integer file descriptor
        fd = isinstance(fd, int) and fd or fd.fileno()

        # get rid of the last registered mask
        self._registry[fd].pop()

        # re-do the previous registration, if any
        if self._registry[fd]:
            self._currentmasks[fd] = self._registry[fd][-1]
        else:
            self._registry.pop(fd)
            self._currentmasks.pop(fd)

    def poll(self, timeout):
        rlist, wlist, xlist = [], [], []
        for fd, eventmask in self._currentmasks.iteritems():
            if eventmask & self.INMASK:
                rlist.append(fd)
            if eventmask & self.OUTMASK:
                wlist.append(fd)
            if eventmask & self.ERRMASK:
                xlist.append(fd)
        rlist, wlist, xlist = select.select(rlist, wlist, xlist, timeout)
        events = collections.defaultdict(int)
        for fd in rlist:
            events[fd] |= self.INMASK
        for fd in wlist:
            events[fd] |= self.OUTMASK
        for fd in xlist:
            events[fd] |= self.ERRMASK
        return events.items()


def best():
    if hasattr(select, 'epoll'):
        return Epoll()
    if hasattr(select, 'kqueue'):
        return KQueue()
    elif hasattr(select, 'poll'):
        return Poll()
    return Select()


def set(poller=None):
    state.poller = poller or best()
set()
