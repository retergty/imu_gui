"""Export a local VIB2 log without overwriting an existing or publishing a partial CSV."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path, nargs='?')
    args = parser.parse_args()
    output = args.output or args.input.with_suffix('.csv')
    converter = Path(__file__).resolve().parents[4] / 'app' / 'imu_vibration_csv.py'
    try:
        if os.path.lexists(output):
            parser.exit(1, f'目标已存在，未覆盖：{output}\n')
        with NamedTemporaryFile(dir=output.parent) as tmp:
            result = subprocess.run([sys.executable, converter, '--', args.input], stdout=tmp)
            if result.returncode:
                return 1
            # Publish the completed file atomically; never replace a concurrent export.
            os.link(tmp.name, output)
    except OSError as error:
        parser.exit(1, f'{error}\n')
    print(output.absolute())
    return 0


if __name__ == '__main__':
    sys.exit(main())
