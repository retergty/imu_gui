"""Shared vocabulary of factory waveform records and report axes."""

AXES = ('x', 'y', 'z')
SENSORS = (('accel', 'Accelerometer', 'm/s²'), ('gyro', 'Gyroscope', 'rad/s'))
MOTORS = {'main_rpm': 'Main motor', 'tail_rpm': 'Tail motor'}
RPM_FIELDS = ('rpm_mean', 'rpm_min', 'rpm_max', 'rpm_count')
SWEEP_POINTS = range(1, 21)
REFERENCE_POINTS = (0, 21)
