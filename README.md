# 飞控 IMU 工作台

分发版无需安装 Python：解压对应平台的完整压缩包，Windows 双击 `IMUWorkbench.exe`，Linux 双击 `IMUWorkbench`。

开发者在项目根目录启动：`.venv/bin/python app/imu_gui.py`。安装、Windows 启动和打包见 [GUI 使用说明](docs/GUI使用说明.md)。

- `app/`：程序和命令行工具；`tests/`：测试；`docs/`：使用说明。
- `logs/raw/`：原始波形；`logs/csv/`：导出的采样数据。
- `logs/reports/`：分析报告和图表；`logs/debug/`：调试日志归档位置。

这些目录用于归档。转换工具未指定输出时仍在输入文件旁生成 CSV。

## 代码结构与验证

- **界面**：`imu_gui.py` 负责主窗口、连接与页面协调；`imu_analysis_panel.py` 管理分析任务、临时预览和报告视图，`imu_test_panel.py` 管理台架测试。设备 I/O 和报告生成在 `imu_workers.py` 的工作线程执行。
- **离线分析**：`imu_vibration_csv.py` 解码 VIB2；`imu_point_stats.py` 按传感器和测点统计；`imu_plot.py` 绘图。`imu_workbench.py` 组织单文件报告，`imu_compare.py` 复用已有报告进行多波形对比。这些模块不依赖 Qt。
- **设备通信**：GUI 与命令行共用 `imu_vibration_download.py` 的目录与下载逻辑，以及 `imu_shell_transport.py` 的 NSH 帧处理。POSIX 交互终端仅由 `mavlink_shell.py` 引入。

从项目根目录运行，不需要连接飞控，也不依赖 `logs/` 下的本地文件：

```bash
.venv/bin/python -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen .venv/bin/python app/imu_gui.py --smoke-test
```
