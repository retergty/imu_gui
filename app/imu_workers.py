import struct
from queue import Empty, Queue
from time import monotonic

from imu_shell_transport import send_shell, shell_payload
from imu_vibration_download import download_file, list_files
from imu_workbench import analyze
from pymavlink import mavftp, mavutil
from PySide6.QtCore import QThread, Signal


class Analysis(QThread):
    result = Signal(object, str, object)
    error = Signal(str)

    def __init__(self, *args, operation=analyze):
        super().__init__()
        self.args = args
        self.operation = operation

    def run(self):
        try:
            figures = {}
            self.result.emit(*self.operation(*self.args, figures=figures), figures)
        except Exception as exc:  # noqa: BLE001 - relay worker failures to the UI thread
            self.error.emit(str(exc))


class Connection(QThread):
    parameter = Signal(str, float)
    ready = Signal()
    files = Signal(object)
    saved = Signal(object)
    idle = Signal()
    output = Signal(str)
    progress = Signal(int)
    error = Signal(str)

    def __init__(self, device, baud):
        super().__init__()
        self.device, self.baud = device, baud
        self.jobs = Queue()

    def run(self):
        link = None
        shell_active = False
        try:
            link = mavutil.mavlink_connection(
                self.device, baud=self.baud, source_system=250
            )
            if link.wait_heartbeat(timeout=10) is None:
                raise OSError("10 秒内未收到心跳，请检查端口、波特率及端口占用。")
            self.ready.emit()
            param_component = link.target_component or mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1
            next_ping, last_rx = 0, monotonic()
            ftp = None
            while not self.isInterruptionRequested():
                try:
                    action, args = self.jobs.get(timeout=0.02)
                except Empty:
                    action = None
                if action == "shell":
                    shell_active = True
                    send_shell(link, args.encode("utf-8"))
                elif action == "parameter":
                    name, value = args
                    target = (link.target_system, param_component, name.encode('ascii'))
                    if value is None:
                        link.mav.param_request_read_send(*target, -1)
                    else:
                        link.mav.param_set_send(*target, value, mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
                elif action in ("list", "get"):
                    if shell_active:
                        send_shell(link, b"")
                        shell_active = False
                    try:
                        if ftp is None:
                            ftp = mavftp.MAVFTP(
                                link, link.target_system, link.target_component
                            )
                        if action == "list":
                            self.files.emit(list_files(ftp))
                        else:
                            download_file(
                                ftp, *args, progress=lambda n, *_: self.progress.emit(n)
                            )
                            self.saved.emit(args[1])
                    finally:
                        last_rx = monotonic()
                        self.idle.emit()
                if self.receive_messages(link, param_component):
                    last_rx = monotonic()
                if monotonic() - last_rx > 12:
                    raise OSError("心跳超时，连接已关闭。")
                if monotonic() >= next_ping:
                    link.mav.heartbeat_send(
                        mavutil.mavlink.MAV_TYPE_GCS,
                        mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                        0,
                        0,
                        0,
                    )
                    next_ping = monotonic() + 1
        except Exception as exc:  # noqa: BLE001 - relay worker failures to the UI thread
            self.error.emit(str(exc))
        finally:
            if link is not None:
                try:
                    if shell_active:
                        send_shell(link, b"")
                finally:
                    link.close()

    def receive_messages(self, link, param_component):
        """Drain a bounded batch without starving queued jobs or heartbeat checks."""
        heartbeat_received = False
        for _ in range(100):
            msg = link.recv_match(blocking=False)
            if msg is None:
                break
            if msg.get_srcSystem() != link.target_system:
                continue
            if msg.get_type() == "HEARTBEAT":
                heartbeat_received = True
            elif msg.get_type() == 'PARAM_VALUE' and msg.get_srcComponent() == param_component:
                value = msg.param_value
                if msg.param_id == 'CA_SP0_COUNT' and msg.param_type == mavutil.mavlink.MAV_PARAM_TYPE_INT32:
                    # PX4 transports integer parameters bytewise inside the float field.
                    value, = struct.unpack('<i', struct.pack('<f', value))
                elif msg.param_type != mavutil.mavlink.MAV_PARAM_TYPE_REAL32:
                    continue
                self.parameter.emit(msg.param_id, value)
            elif msg.get_type() == 'SERIAL_CONTROL' and (data := shell_payload(msg)):
                self.output.emit(data.decode('utf-8', 'replace'))
        return heartbeat_received
