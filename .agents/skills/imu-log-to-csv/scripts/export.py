"""Export a local VIB2 log without overwriting an existing or publishing a partial CSV."""
import argparse
import os
import struct
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'app'))
from imu_vibration_csv import convert


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path, nargs='?')
    args = parser.parse_args()
    output = args.output or args.input.with_suffix('.csv')
    if os.path.lexists(output):
        parser.exit(1, f'目标已存在，未覆盖：{output}\n')
    try:
        with TemporaryDirectory(dir=output.parent) as staging:
            partial = Path(staging) / 'samples.csv'
            with args.input.open('rb') as src, partial.open('w', encoding='utf-8', newline='') as dst:
                outcome, count = convert(src, dst)
            # Publish atomically; never replace a file created by a concurrent export.
            os.link(partial, output)
    except (ValueError, OSError, struct.error) as error:
        parser.exit(1, f'{args.input}: {error}\n')
    print(f'{outcome}: {count} records', file=sys.stderr)
    print(output.absolute())


if __name__ == '__main__':
    main()
