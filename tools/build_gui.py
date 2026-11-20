"""Build and smoke-test a native, double-clickable IMU Workbench distribution."""
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = 'IMUWorkbench'


def main():
    system = platform.system()
    if system not in ('Linux', 'Windows'):
        raise SystemExit('Supported build hosts: Windows and Linux')
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm', '--onedir',
        '--windowed', '--name', NAME, '--specpath', str(ROOT / 'build'),
        '--collect-submodules', 'pymavlink.dialects', str(ROOT / 'app/imu_gui.py'),
    ], cwd=ROOT, check=True)
    dist = ROOT / 'dist'
    executable = dist / NAME / (NAME + ('.exe' if system == 'Windows' else ''))
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run([str(executable), '--smoke-test'], cwd=folder, check=True,
                       timeout=90, env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
    archive = shutil.make_archive(str(dist / f'{NAME}-{system}-{platform.machine()}'),
                                  'zip' if system == 'Windows' else 'gztar', dist, NAME)
    print(archive)


if __name__ == '__main__':
    main()
