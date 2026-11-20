"""MAVLink NSH framing shared by the desktop UI and POSIX terminal client."""

from pymavlink import mavutil


def send_shell(link, data, device=10):
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
