#!/usr/bin/env python3
"""List and download factory IMU waveforms over MAVFTP."""
import argparse
import errno
import logging
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic

from pymavlink import mavftp, mavutil

REMOTE_DIR = '/fs/microsd/log/imu_vibration'


def check(result):
    if result.error_code:
        result.display_message()
        raise OSError(f'MAVFTP {result.operation_name} 失败（{result.error_code}）')


def show_progress(percent, received, elapsed):
    filled = percent // 4
    bar = '#' * filled + '-' * (25 - filled)
    speed = received / elapsed / 1000 if elapsed > 0 else 0
    print(f'\r下载进度 [{bar}] {percent:3d}%  平均 {speed:6.1f} kB/s', end='', file=sys.stderr, flush=True)


def list_files(ftp):
    check(ftp.cmd_list([REMOTE_DIR]))
    return sorted((e for e in ftp.list_result if not e.is_dir), key=lambda e: e.name)


def download_file(ftp, entry, destination, progress=show_progress):
    output = destination.parent
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output) as staging:
        partial = Path(staging) / 'download'
        started = last_update = monotonic()

        def update_progress(fraction):
            nonlocal last_update
            now = monotonic()
            if fraction is not None and now - last_update >= 0.5:
                progress(min(99, int(fraction * 100)), min(fraction, 1) * entry.size_b, now - started)
                last_update = now

        progress(0, 0, 0)
        check(ftp.cmd_get([f'{REMOTE_DIR}/{entry.name}', str(partial)], progress_callback=update_progress))
        # 低带宽数传可能需要很久；0 表示不限制下载总时长。
        check(ftp.process_ftp_reply('get', timeout=0))
        if not partial.is_file() or partial.stat().st_size != entry.size_b:
            raise OSError('下载长度与目录条目不一致，请重新列目录并下载。')
        partial.replace(destination)
        progress(100, entry.size_b, monotonic() - started)


def browse(ftp, output):
    files = list_files(ftp)
    if not files:
        print(f'{REMOTE_DIR} 中没有文件。')
        return
    for index, entry in enumerate(files, 1):
        print(f'{index:3}. {entry.name}  ({entry.size_b} B)')

    while True:
        choice = input('输入要下载的文件编号（回车退出）：').strip()
        if not choice:
            return
        if choice.isdecimal() and 1 <= int(choice) <= len(files):
            break
        print(f'请输入 1 到 {len(files)} 之间的编号。')

    entry = files[int(choice) - 1]
    if not entry.name or Path(entry.name).name != entry.name or entry.name in ('.', '..'):
        raise ValueError('远端条目不是有效文件名')
    output.mkdir(parents=True, exist_ok=True)
    destination = output / entry.name
    if destination.exists() and input(f'{destination} 已存在，覆盖？[y/N] ').strip().lower() != 'y':
        return
    try:
        download_file(ftp, entry, destination)
    finally:
        print(file=sys.stderr)
    print(f'已保存：{destination.resolve()}  ({entry.size_b} B)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True, help='指定 MAVLink 串口或连接地址')
    parser.add_argument('--baud', type=int, default=115200, help='串口波特率')
    parser.add_argument('--output', type=Path, default=Path.cwd(), help='保存目录（默认当前目录）')
    parser.add_argument('--ftp-log', type=Path, help='将 MAVFTP 重试、补包等诊断信息追加到指定日志')
    args = parser.parse_args()
    if args.ftp_log:
        logging.basicConfig(filename=args.ftp_log, level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    link = mavutil.mavlink_connection(args.device, baud=args.baud, source_system=250)
    try:
        if link.wait_heartbeat(timeout=10) is None:
            raise OSError('未收到飞控心跳，请检查端口和波特率。')
        ftp = mavftp.MAVFTP(link, link.target_system, link.target_component)
        if args.ftp_log:
            check(ftp.cmd_set(['debug', '1']))
        browse(ftp, args.output)
    finally:
        link.close()


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.exit('已取消。')
    except (OSError, ValueError) as error:
        sys.exit('串口正忙，请退出占用该端口的 QGC 或串口工具。' if getattr(error, 'errno', None) == errno.EBUSY else str(error))
