import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from imu_test_panel import VibrationPanel
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.panel = VibrationPanel()
        self.addCleanup(self.panel.close)
        self.sent = []
        self.panel.command.connect(self.sent.append)

    def test_new_status_scrolls_to_bottom_after_reading_history(self):
        p = self.panel
        p.show()
        p.output.setPlainText('历史状态\n' * 100)
        self.app.processEvents()
        bar = p.output.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(bar.minimum())
        p.receive_test_message('motor_vibration accepted; use status to observe completion')
        self.app.processEvents()
        self.assertTrue(p.output.toPlainText().endswith('飞控已受理振动测试，等待状态确认。'))
        self.assertEqual(bar.value(), bar.maximum())

    def test_point_hints_follow_selection_and_mode(self):
        p = self.panel
        self.assertEqual('第 1 点：5%', p.first.toolTip())
        p.last.setValue(20)
        p.first.setValue(10)
        self.assertIn('第 10 点：50% 至 第 20 点：100%', p.points_label.toolTip())
        p.mode.setCurrentIndex(1)
        self.assertIn('尾电机推力指令', p.points_label.toolTip())
        p.mode.setCurrentIndex(2)
        self.assertIn('主电机推力指令', p.points_label.toolTip())
        self.assertIn('配对曲线', p.points_label.toolTip())
        self.assertEqual(p.first.toolTip(), '第 10 点：50%')
        self.assertEqual(p.last.toolTip(), '第 20 点：100%')
        self.assertEqual(p.points_separator.toolTip(), '第 10 点：50% 至 第 20 点：100%')
        self.assertEqual(self.sent, [])

    def test_trim_read_step_and_disconnect(self):
        p = self.panel
        requests = []
        p.parameter_request.connect(lambda n, v: requests.append((n, v)))
        p.set_available(True)
        p.refresh_trim.click()
        p.parameter_received('CA_SP0_COUNT', 3)
        names = [f'CA_SV_CS{i}_TRIM' for i in range(3)]
        for name in names:
            p.parameter_received(name, 0.2)
        self.assertEqual(requests, [(n, None) for n in ['CA_SP0_COUNT', *names]])
        spin = p.trims[names[1]]
        spin.stepUp()
        self.assertEqual(requests[-1], (names[1], 0.21))
        self.assertFalse(spin.isEnabled())
        p.parameter_received(names[1], 0.21)
        self.assertTrue(spin.isEnabled())
        self.assertEqual(len(requests), 5)
        p.set_available(False)
        self.assertFalse(p.trims)
        self.assertFalse(p.refresh_trim.isEnabled())

    def test_trim_typing_and_buttons(self):
        p = self.panel
        p.show()
        p.set_available(True)
        p.parameter_received('CA_SP0_COUNT', 2)
        name = 'CA_SV_CS0_TRIM'
        p.parameter_received(name, 0)
        p.parameter_received('CA_SV_CS1_TRIM', 0)
        writes = []
        p.parameter_request.connect(lambda n, v: writes.append((n, v)))
        spin = p.trims[name]
        spin.setFocus()
        spin.selectAll()
        QTest.keyClicks(spin, '-0.35')
        self.assertEqual(writes, [])
        QTest.keyClick(spin, Qt.Key.Key_Return)
        self.assertEqual(writes, [(name, -0.35)])
        p.parameter_received(name, -0.35)
        buttons = p.trim_fields.findChildren(QPushButton)
        for hint, expected in [('增加 0.01', -0.34), ('减少 0.01', -0.35)]:
            btn = next(b for b in buttons if b.accessibleName() == f'{name} {hint}')
            btn.click()
            self.assertEqual(writes[-1], (name, expected))
            self.assertFalse(btn.isEnabled())
            p.parameter_received(name, expected)
        self.assertEqual(len(writes), 3)

    @patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes)
    def test_points_and_save(self, _confirm):
        p = self.panel
        p.first.setValue(3)
        p.mode.setCurrentIndex(2)
        p.begin.click()
        self.assertEqual(self.sent[-1], 'factory_test begin motor_vibration 3 3 2')
        p.last.setValue(5)
        p.save.setChecked(True)
        p.begin.click()
        self.assertEqual(p.name.text(), "imu_raw.bin")
        self.assertEqual(self.sent[-1], "factory_test begin motor_vibration 3 5 2 imu_raw.bin")
        p.name.clear()
        p.begin.click()
        self.assertEqual(self.sent[-1], "factory_test begin motor_vibration 3 5 2 imu_raw.bin")
        p.name.setText('bench.bin')
        p.begin.click()
        self.assertEqual(self.sent[-1], 'factory_test begin motor_vibration 3 5 2 bench.bin')

    @patch.object(QMessageBox, 'warning')
    @patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No)
    def test_rejected_commands(self, confirm, warning):
        p = self.panel
        p.set_available(False)
        p.begin.click()
        confirm.assert_not_called()
        p.set_available(True)
        p.save.setChecked(True)
        p.name.setText('a; reboot')
        p.begin.click()
        warning.assert_called_once()
        confirm.assert_not_called()
        p.save.setChecked(False)
        p.begin.click()
        confirm.assert_called_once()
        self.assertEqual(self.sent, [])

    @patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes)
    def test_status_polls_and_translates_chunked_firmware_output(self, _confirm):
        p = self.panel
        p.set_available(True)
        p.begin.click()
        self.assertTrue(p.status_poll.isActive())
        self.assertEqual(p.status_poll.interval(), 10_000)
        p.receive_shell('nsh> factory_test begin motor_vibration 1 1 0\r\n'
                        'INFO  [factory_test] motor_vibration accepted; use status to observe completion\r\nnsh> ')
        p.status_poll.timeout.emit()
        self.assertEqual(self.sent[-1], 'factory_test status')
        p.status.click()
        self.assertEqual(self.sent.count('factory_test status'), 1)
        reply = ('factory_test status\r\n\x1b[32mINFO  [factory_test] run: 7, '
                 'command: motor_vibration, state: RUNNING\x1b[0m\r\n'
                 'INFO  [factory_test] progress: 35%, reason: none\r\n'
                 'INFO  [factory_test] vibration point 3: waiting for stable RPM\r\n'
                 'INFO  [factory_test] log: active; handle: 1; run: 7; terminal: no; complete: no\r\nnsh> ')
        for byte in reply:
            p.receive_shell(byte)
        rendered = p.output.toPlainText()
        for text in ('测试进行中', '进度 35%', '第 3 测点：正在等待转速稳定', '波形：正在写入'):
            self.assertIn(text, rendered)
        for raw in ('nsh>', 'factory_test', 'RUNNING'):
            self.assertNotIn(raw, rendered)
        self.assertTrue(p.status_poll.isActive())

        p.set_available(False)
        p.status_poll.timeout.emit()
        self.assertEqual(self.sent.count('factory_test status'), 1)
        p.set_available(True)
        p.status_poll.timeout.emit()
        p.receive_shell('run: 7, command: motor_vibration, state: COMPLETE\r\n'
                        'progress: 100%, reason: none\r\n'
                        'log: closed; handle: 1; run: 7; terminal: yes; complete: yes\r\n'
                        'log path: /fs/microsd/log/bench.bin; accepted/written: 512/512; errno: 0\r\nnsh> ')
        self.assertFalse(p.status_poll.isActive())
        self.assertIn('已保存到飞控 SD 卡', p.output.toPlainText())
        self.assertIn('已写入 512/512 字节', p.output.toPlainText())

    @patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes)
    def test_status_handles_rejection_timeout_abort_and_disconnect(self, _confirm):
        p = self.panel
        p.set_available(True)
        p.begin.click()
        p.receive_shell('ERROR [factory_test] motor_vibration: system is not disarmed\r\nnsh> ')
        self.assertFalse(p.status_poll.isActive())
        self.assertIn('飞控未处于上锁状态', p.output.toPlainText())
        p.begin.click()
        p.status_poll.timeout.emit()
        p.status_timeout.stop()
        p.status_timeout.timeout.emit()
        self.assertIn('不能确认测试是否结束', p.output.toPlainText())
        self.assertTrue(p.status_poll.isActive())
        p.status_poll.timeout.emit()
        p.receive_shell('run: 8, command: motor_vibration, state: ABORTED\r\n'
                        'progress: 35%, reason: user abort\r\n'
                        'log: closed; handle: 1; run: 7; terminal: yes; complete: yes\r\nnsh> ')
        self.assertFalse(p.status_poll.isActive())
        self.assertIn('用户请求中止', p.output.toPlainText())
        self.assertNotIn('已保存', p.output.toPlainText())
        p.begin.click()
        p.status_poll.timeout.emit()
        p.reset_status_connection()
        self.assertFalse(p.status_poll.isActive())
        self.assertFalse(p.status_timeout.isActive())
        self.assertIn('测试是否结束尚未确认', p.output.toPlainText())
