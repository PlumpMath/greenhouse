import functools
import time
import weakref

from greenhouse import compat, scheduler, utils


__all__ = ["wait_fds"]


def wait_fds(fd_events, inmask=1, outmask=2, timeout=None):
    '''wait for the first of multiple descriptors with the greenhouse poller

    `fd_events` is a list of 2-tuples mapping integer file descriptors to
    masks, which include bitwise ORed `inmask` for read, `outmask` for write.

    if timeout is None it can wait indefinitely, if a nonzero number then it
    will not wait longer, if zero then the current coroutine will still be
    paused, but will awake around again in the very next mainloop iteration.

    the return value is a list of (fd, event) pairs where fd is one of the
    descriptors from fd_events, and event is `inmask` or `outmask`, or both
    bitwise ORed if both apply (the events returned for a fd will be a subset
    of those asked for in `fd_events`).
    '''
    current = compat.getcurrent()
    poller = scheduler.state.poller
    dmap = scheduler.state.descriptormap
    activated = {}
    fakesocks = {}
    awoken = [False]

    def activate(fd, event):
        if not activated and (timeout or timeout is None):
            scheduler.schedule(current)
            if timeout and not awoken[0]:
                utils.Timer._remove_from_timedout(waketime, current)
                awoken[0] = True
        activated.setdefault(fd, 0)
        activated[fd] |= event

    registrations = {}
    for fd, events in fd_events:
        fakesock = _FakeSocket()
        fakesocks[fd] = fakesock
        poller_events = 0

        if events & inmask:
            fakesock._readable.set = functools.partial(activate, fd, inmask)
            poller_events |= poller.INMASK

        if events & outmask:
            fakesock._writable.set = functools.partial(activate, fd, outmask)
            poller_events |= poller.OUTMASK

        dmap[fd].append(weakref.ref(fakesock))

        registrations[fd] = poller.register(fd, poller_events)

    if timeout:
        # real timeout value, schedule outself `timeout` seconds in the future
        waketime = time.time() + timeout
        scheduler.pause_until(waketime)
    elif timeout is not None:
        # timeout == 0, only pause for 1 loop iteration
        scheduler.pause()
    else:
        # timeout is None, it's up to _hit_poller->activate to bring us back
        scheduler.state.mainloop.switch()

    for fd, reg in registrations.iteritems():
        poller.unregister(fd, reg)

    return activated.items()


class _FakeSocket(object):
    _closed = False

    def __init__(self):
        self._readable = _FakeEvent()
        self._writable = _FakeEvent()

class _FakeEvent(object):
    def set(self):
        pass

    def clear(self):
        pass
