import io
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from imu_shell_transport import shell_payload
from imu_vibration_download import download_file, list_files


class TransportTests(unittest.TestCase):
    def test_directory_and_direct_download_share_filename_rules(self):
        ftp = Mock()
        ftp.cmd_list.return_value.error_code = 0
        names = ('a.bin', 'b.bin', '', '.', '..', '../x', 'C:x', r'..\x', '/x')
        entries = [SimpleNamespace(name=name, is_dir=False) for name in names]
        ftp.list_result = [*reversed(entries), SimpleNamespace(name='folder', is_dir=True)]
        self.assertEqual(list_files(ftp), entries[:2])
        for entry in entries[2:]:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                download_file(ftp, entry, Path('unused'))
        ftp.cmd_get.assert_not_called()

    def test_shell_ignores_padding_and_other_devices(self):
        msg = SimpleNamespace(device=10, count=2, data=b'ok\x00\x00')
        self.assertEqual(shell_payload(msg), b'ok')
        msg.device = 0
        self.assertEqual(shell_payload(msg), b'')

    @unittest.skipUnless(os.name == 'posix', 'POSIX terminal client')
    def test_shell_history_handles_fragmented_arrow_and_backspace(self):
        from mavlink_shell import LineEditor
        editor = LineEditor(io.StringIO())
        self.assertEqual(list(editor.feed('abc\b\n\x1b')), ['ab\n'])
        self.assertEqual(list(editor.feed('[A!\n')), ['ab!\n'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX terminal client')
    def test_shell_releases_exclusive_session_after_interruption(self):
        import mavlink_shell as shell
        link = Mock()
        with (patch.object(sys, 'argv', ['shell', 'mock-port']),
              patch.object(shell.mavutil, 'mavlink_connection', return_value=link),
              patch.object(shell, 'terminal_input'), patch.object(sys, 'stdin'),
              patch.object(shell, 'interact', side_effect=KeyboardInterrupt),
              self.assertRaises(KeyboardInterrupt)):
            shell.main()
        self.assertEqual([call.args[4] for call in link.mav.serial_control_send.call_args_list], [1, 0])
        link.close.assert_called_once()
