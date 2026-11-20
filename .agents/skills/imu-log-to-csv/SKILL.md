---
name: imu-log-to-csv
description: 当用户要求把本项目的 imu_log、产测 IMU 振动日志或 factory_test VIB2 波形转成 CSV 时使用；包括以 .bin 或 .ulg 命名的 VIB2 文件，不用于普通 PX4 ULog。
---

在项目根目录运行以下命令（第二个路径可省略）。入口调用项目已有的 `app/imu_vibration_csv.py` 校验并解析波形；不要仅凭扩展名判断格式。

```bash
.venv/bin/python .agents/skills/imu-log-to-csv/scripts/export.py '输入日志路径' '输出CSV路径'
```

- 默认在输入旁生成同名 `.csv`。已有目标不会被覆盖；如遇重名，请用户指定新路径。批量请求按指定范围逐个调用；未指定输入且有多个候选时，请用户指明。
- 命令退出码为 0 时，报告 CSV 路径、样本数和日志中的产测结果。`failed`、`cancelled` 是产测结果，不是转换错误。退出码非零时报告具体原因，不交付部分结果。
- 保留原始行序和两路独立采样，不配对、插值或更改单位。`timestamp_sample_us` 为微秒，`accel` 三轴为 m/s²，`gyro` 三轴为 rad/s。

格式依据：[产测 IMU 波形记录与验证](/home/chenhuaming/PX4/PX4_BACKUP/PX4_BACKUP/develop_doc/factory_test_imu_waveform_log_plan.md)。文档仅作格式参考；转换不需要连接设备、下载日志或启动产测。
