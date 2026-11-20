import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from imu_test_panel import VibrationPanel


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.panel = VibrationPanel()
        self.addCleanup(self.panel.close)
        self.sent = []
        self.panel.command.connect(self.sent.append)

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
