"""MAVLink NSH framing shared by the desktop UI and POSIX terminal client."""

from pymavlink import mavutil

SHELL_DEVICE = 10


def shell_payload(msg):
    """Return only bytes addressed to the NSH device from a SERIAL_CONTROL frame."""
    return bytes(msg.data[:msg.count]) if msg.device == SHELL_DEVICE else b''


def send_shell(link, data, device=SHELL_DEVICE):
    # An empty frame with flags=0 releases the remote exclusive session.
    flags = (
        (
            mavutil.mavlink.SERIAL_CONTROL_FLAG_EXCLUSIVE
            | mavutil.mavlink.SERIAL_CONTROL_FLAG_RESPOND
        )
        if data
        else 0
    )
    for i in range(0, len(data) or 1, 70):
        chunk = data[i : i + 70]
        link.mav.serial_control_send(
            device, flags, 0, 0, len(chunk), chunk.ljust(70, b"\0")
        )
