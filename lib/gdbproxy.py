#!/usr/bin/env python3

"""
Run GDB in a pty.

This will allow to inject server commands not exposing them
to a user.
"""

import argparse
import array
import errno
import fcntl
import os
import pty
import select
import signal
import socket
import termios
import tty

import StreamFilter


class GdbProxy(object):
    """This class does the actual work of the pseudo terminal."""

    def __init__(self, server_address, argv):
        """Create a spawned process."""

        if server_address:
            # Make sure the socket does not already exist
            try:
                os.unlink(server_address)
            except OSError:
                if os.path.exists(server_address):
                    raise
            # Create a UDS socket
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.sock.bind(server_address)
            self.sock.settimeout(0.5)
        else:
            self.sock = None

        # Create the filter
        self.filter = StreamFilter.StreamFilter(b"server nvim-gdb-",
                                                b"\n(gdb) ")

        pid, self.master_fd = pty.fork()
        if pid == pty.CHILD:
            os.execlp(argv[0], *argv)

        old_handler = signal.signal(signal.SIGWINCH,
                                    lambda signum, frame: self._set_pty_size())

        mode = tty.tcgetattr(pty.STDIN_FILENO)
        tty.setraw(pty.STDIN_FILENO)

        self._set_pty_size()

        try:
            self._process()
        except Exception:
            pass

        tty.tcsetattr(pty.STDIN_FILENO, tty.TCSAFLUSH, mode)

        os.close(self.master_fd)
        self.master_fd = None
        signal.signal(signal.SIGWINCH, old_handler)

        if server_address:
            # Make sure the socket does not already exist
            try:
                os.unlink(server_address)
            except OSError:
                pass

    def _set_pty_size(self):
        """Set the window size of the child pty."""
        assert self.master_fd is not None

        buf = array.array('h', [0, 0, 0, 0])
        fcntl.ioctl(pty.STDOUT_FILENO, termios.TIOCGWINSZ, buf, True)
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, buf)

    def _process(self):
        """Run the main loop."""
        sockets = [self.master_fd, pty.STDIN_FILENO]
        if self.sock:
            sockets.append(self.sock)

        while True:
            try:
                rfds, wfds, xfds = select.select(sockets, [], [], 1.0)
                select.select
            except select.error as e:
                if e[0] == errno.EAGAIN:   # Interrupted system call.
                    continue
                else:
                    raise

            if not rfds:
                self._timeout()
            else:
                if self.master_fd in rfds:
                    data = os.read(self.master_fd, 1024)
                    self.master_read(data)
                if pty.STDIN_FILENO in rfds:
                    data = os.read(pty.STDIN_FILENO, 1024)
                    self.stdin_read(data)
                if self.sock in rfds:
                    data, addr = self.sock.recvfrom(65536)
                    self.write_master(data)

    def _write(self, fd, data):
        """Write the data to the file."""
        while data:
            n = os.write(fd, data)
            data = data[n:]

    def _timeout(self):
        data = self.filter.Timeout()
        self._write(pty.STDOUT_FILENO, data)

    def write_stdout(self, data):
        """Write to stdout for the child process."""
        data = self.filter.Filter(data)
        self._write(pty.STDOUT_FILENO, data)

    def write_master(self, data):
        """Write to the child process from its controlling terminal."""
        self._write(self.master_fd, data)

    def master_read(self, data):
        """Handle data from the child process."""
        self.write_stdout(data)

    def stdin_read(self, data):
        """Handle data from the controlling terminal."""
        self.write_master(data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
            description="Run GDB through a filtering proxy.")
    parser.add_argument('gdb', metavar='GDB', help='GDB command')
    parser.add_argument('args', metavar='ARGS', nargs='*',
                        help='GDB arguments')
    parser.add_argument('-a', '--address', metavar='ADDR',
                        help='Local socket to receive commands.')
    args = parser.parse_args()

    GdbProxy(args.address, [args.gdb] + args.args)
