"""Shared desktop workflows (no UI or platform terminal dependencies)."""

from pathlib import Path
from tempfile import mkdtemp
from shutil import rmtree

from imu_vibration_csv import convert
from imu_point_stats import generate_report


def analyze(path, output):
    """Create a fresh report; never overwrite previous runs or expose partial results on error."""
    with path.open("rb") as src:
        magic = src.read(4)
    if magic != b"VIB2" and (magic == b"ULog" or path.suffix.lower() != ".csv"):
        raise ValueError("仅支持 VIB2 波形和已有采样 CSV；不支持普通 PX4 ULog。")
    folder = Path(mkdtemp(prefix=f"{path.stem}_stats_", dir=output))
    try:
        csv_path = path
        outcome = "输入为 CSV（无产测结果）"
        if magic == b"VIB2":
            csv_path = folder / "samples.csv"
            with (
                path.open("rb") as src,
                csv_path.open("w", encoding="utf-8", newline="") as dst,
            ):
                status, count = convert(src, dst)
            outcome = f"转换成功：{count} 条；产测结果：{status}"
            if not count:
                return folder, outcome + "；无采样可分析"
        groups, levels = generate_report(csv_path, folder)
        peak = max(levels, key=levels.get) if levels else None
        detail = (
            f"最大振动：测点 {peak}，{levels[peak]:.4f} m/s²"
            if levels
            else "无加速度计采样"
        )
        return (
            folder,
            f"{outcome}；统计 {sum(g.count for g in groups.values())} 条。{detail}",
        )
    except Exception:
        rmtree(folder)
        raise
