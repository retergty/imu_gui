#!/usr/bin/env python3
"""Open an interactive POSIX shell over MAVLink (original client by Beat Kueng)."""
import argparse
import codecs
import os
import select
import sys
import termios
from contextlib import closing, contextmanager
from time import monotonic

from imu_shell_transport import send_shell, shell_payload
from pymavlink import mavutil
from serial import SerialException


def detect_port():
    if sys.platform == 'darwin':
        return '/dev/tty.usbmodem01'
    ports = mavutil.auto_detect_serial(preferred_list=[
        '*FTDI*', '*Arduino_Mega_2560*', '*3D_Robotics*', '*USB_to_UART*', '*PX4*', '*FMU*', '*Gumstix*',
    ])
    if not ports:
        return None
    if len(ports) > 1:
        print('Auto-detected serial ports are:', *(f' {port}' for port in ports), sep='\n')
    print(f'Using port {ports[0]}')
    return ports[0].device


@contextmanager
def terminal_input(fd):
    """Keep signal handling, but let the editor handle echo and line buffering."""
    if not os.isatty(fd):
        yield
        return
    original = termios.tcgetattr(fd)
    edited = original.copy()
    edited[3] &= ~(termios.ECHO | termios.ICANON)
    termios.tcsetattr(fd, termios.TCSANOW, edited)
    try:
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)


class LineEditor:
    def __init__(self, output):
        self.output = output
        self.line = self.escape = ''
        self.history = []
        self.index = 0

    def erase(self, count):
        if count:
            self.output.write(f'\x1b[{count}D\x1b[K')

    def feed(self, text):
        """Echo edits locally and yield only completed lines to the flight controller."""
        for ch in text:
            if self.escape:
                if self.escape == '\x1b' and ch == '[':
                    self.escape += ch
                    continue
                if self.escape == '\x1b[' and ch in 'AB':
                    self.index = min(len(self.history), max(0, self.index + (-1 if ch == 'A' else 1)))
                    self.erase(len(self.line))
                    self.line = self.history[self.index] if self.index < len(self.history) else ''
                    self.output.write(self.line)
                self.escape = ''
            elif ch == '\x1b':
                self.escape = ch
            elif ch == '\n':
                self.erase(len(self.line))
                if self.line and self.history[-1:] != [self.line]:
                    self.history.append(self.line)
                    del self.history[:-50]
                self.index = len(self.history)
                yield self.line + '\n'
                self.line = ''
            elif ch in ('\b', '\x7f'):
                if self.line:
                    self.erase(1)
                    self.line = self.line[:-1]
            elif ord(ch) > 3:
                self.line += ch
                self.output.write(ch)


def heartbeat(link):
    link.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GENERIC, mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)


def interact(link, fd):
    editor = LineEditor(sys.stdout)
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    next_ping = 0
    deadline = None
    while deadline is None or monotonic() < deadline:
        if deadline is None and select.select([fd], [], [], 0)[0]:
            data = os.read(fd, 4096)
            if data:
                for line in editor.feed(decoder.decode(data)):
                    send_shell(link, line.encode('utf-8'))
            else:
                deadline = monotonic() + 1  # Allow the last command's reply after EOF.
        msg = link.recv_match(type='SERIAL_CONTROL', blocking=True, timeout=0.03)
        if msg is not None and msg.get_srcSystem() == link.target_system:
            sys.stdout.write(shell_payload(msg).decode('latin-1'))
        sys.stdout.flush()
        if monotonic() >= next_ping:
            heartbeat(link)
            next_ping = monotonic() + 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('port', metavar='PORT', nargs='?', help='Serial device or MAVLink address (default: auto-detect)')
    parser.add_argument('--baudrate', '-b', type=int, default=57600, help='Serial baud rate (default: 57600)')
    args = parser.parse_args()
    port = args.port or detect_port()
    if not port:
        print('Error: no serial connection found')
        return
    print('Connecting to MAVLINK...')
    with closing(mavutil.mavlink_connection(port, autoreconnect=True, baud=args.baudrate)) as link:
        heartbeat(link)
        link.wait_heartbeat()
        try:
            send_shell(link, b'\n')
            fd = sys.stdin.fileno()
            with terminal_input(fd):
                interact(link, fd)
        finally:
            send_shell(link, b'')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except SerialException as exc:
        sys.exit(str(exc))
