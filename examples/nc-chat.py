#!/usr/bin/env python

import greenhouse


PORT = 9000
CONNECTED = {}

def start():
    print "localhost nc chat server starting on port %d." % PORT
    print "shut it down with <Ctrl>-C"

    try:
        serversock = greenhouse.Socket()
        serversock.bind(("", PORT))
        serversock.listen(5)

        while 1:
            clientsock, address = serversock.accept()
            greenhouse.schedule(get_name, args=(clientsock,))

    except KeyboardInterrupt:
        print "KeyboardInterrupt caught, closing connections"
        serversock.close()
        for sock in CONNECTED.values():
            sock.close()

def get_name(clientsock):
    clientsock.sendall("enter your name up to 20 characters\n")
    name = clientsock.recv(8192).rstrip("\r\n")

    if len(name) > 20:
        clientsock.close()
        return

    CONNECTED[name] = clientsock

    greenhouse.schedule(broadcast,
        args=("*** %s has entered\n" % name,),
        kwargs={'continuation': lambda: connection_handler(clientsock, name)})

def broadcast(msg, skip=None, continuation=None):
    for recip in CONNECTED:
        sock = CONNECTED.get(recip)
        if sock and not sock._closed and skip != recip:
            sock.sendall(msg)

    if continuation:
        greenhouse.schedule(continuation)

def connection_handler(clientsock, name):
    while 1:
        if clientsock._closed:
            CONNECTED.pop(name)
            break

        input = received = clientsock.recv(8192)
        if not input:
            CONNECTED.pop(name)
            break

        while len(input) == 8192:
            input = clientsock.recv(8192)
            received += input

        greenhouse.schedule(broadcast, args=(
            "%s: %s\n" % (name, received.rstrip("\r\n")), name))

    broadcast("*** %s has left the building\n" % name)


if __name__ == "__main__":
    start()
