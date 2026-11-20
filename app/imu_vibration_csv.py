#!/usr/bin/env python3
"""Convert a factory_test VIB2 waveform to CSV on stdout; report the outcome on stderr."""
import argparse
import csv
import os
import struct
import sys

SAMPLE = struct.Struct('<BBBBQfff')
TRAILER = struct.Struct('<4sII')
OUTCOMES = ('success', 'failed', 'cancelled')


def convert(source, output):
    """Check the header, trailer and length before streaming records."""
    size = source.seek(0, os.SEEK_END)
    source.seek(0)
    if size < 16 or source.read(4) != b'VIB2':
        raise ValueError('unknown header or truncated waveform')
    source.seek(-TRAILER.size, os.SEEK_END)
    magic, code, count = TRAILER.unpack(source.read(TRAILER.size))
    if magic != b'END2' or code >= len(OUTCOMES) or size != 16 + count * SAMPLE.size:
        raise ValueError('invalid trailer or record count; waveform is incomplete')
    source.seek(4)
    writer = csv.writer(output)
    extra = ('rpm_mean', 'rpm_min', 'rpm_max', 'rpm_count')
    writer.writerow(('source', 'point', 'phase', 'counted', 'timestamp_sample_us', 'x', 'y', 'z') + extra)
    for _ in range(count):
        raw = source.read(SAMPLE.size)
        if raw[0] in (2, 3):
            motor, point, reserved, n, mean, lo, hi, pad = struct.unpack('<BBHIfffI', raw)
            if point > 21 or reserved or pad:
                raise ValueError('invalid VIB2 RPM summary')
            writer.writerow((('main_rpm', 'tail_rpm')[motor - 2], point, 'point_summary', '', '', '', '', '',
                             mean if n else '', lo if n else '', hi if n else '', n))
            continue
        sensor, point, phase, flags, timestamp, x, y, z = SAMPLE.unpack(raw)
        name = {2: 'baseline', 5: 'sample', 7: 'final_reference'}.get(phase)
        if (sensor > 1 or flags not in (0, 2) or not timestamp or not name
                or (phase == 2 and point != 0) or (phase == 7 and point != 21)
                or (phase == 5 and not 1 <= point <= 20)):
            raise ValueError('invalid VIB2 sample fields')
        writer.writerow((('accel', 'gyro')[sensor], point, name, int(bool(flags)), timestamp, x, y, z) + ('',) * len(extra))
    return OUTCOMES[code], count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('waveform')
    args = parser.parse_args()
    try:
        with open(args.waveform, 'rb') as stream:
            outcome, records = convert(stream, sys.stdout)
        print(f'{outcome}: {records} records', file=sys.stderr)
    except (ValueError, OSError, struct.error) as error:
        parser.exit(1, f'{args.waveform}: {error}\n')
