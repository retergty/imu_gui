# 飞控 IMU 工作台

分发版无需安装 Python：解压对应平台的完整压缩包，Windows 双击 `IMUWorkbench.exe`，Linux 双击 `IMUWorkbench`。

开发者在项目根目录启动：`.venv/bin/python app/imu_gui.py`。安装、Windows 启动和打包见 [GUI 使用说明](docs/GUI使用说明.md)。

- `app/`：程序和命令行工具；`tests/`：测试；`docs/`：使用说明。
- `logs/raw/`：原始波形；`logs/csv/`：导出的采样数据。
- `logs/reports/`：分析报告和图表；`logs/debug/`：调试日志归档位置。

这些目录用于归档。转换工具未指定输出时仍在输入文件旁生成 CSV。
