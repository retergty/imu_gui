"""Shared desktop workflows (no UI or platform terminal dependencies)."""

from contextlib import contextmanager
from pathlib import Path
from shutil import copytree, rmtree
from tempfile import mkdtemp

from imu_point_stats import WaveformSummary, generate_report
from imu_vibration_csv import convert


@contextmanager
def report_directory(parent, prefix):
    """Keep a fresh directory on success, discard incomplete output on failure."""
    folder = Path(mkdtemp(prefix=prefix, dir=parent))
    try:
        yield folder
    except BaseException:
        rmtree(folder, ignore_errors=True)
        raise


def copy_report(report, destination, source):
    if destination.resolve().is_relative_to(report.resolve()):
        raise ValueError('请选择当前报告目录以外的位置。')
    with report_directory(destination, f'{source.stem}_stats_') as target:
        copytree(report, target, dirs_exist_ok=True)
        return target


def analyze(path, output, figures=None):
    """Create a fresh report; never overwrite previous runs or expose partial results on error."""
    with path.open("rb") as src:
        magic = src.read(4)
    if magic != b"VIB2" and (magic == b"ULog" or path.suffix.lower() != ".csv"):
        raise ValueError("仅支持 VIB2 波形和已有采样 CSV；不支持普通 PX4 ULog。")
    with report_directory(output, f"{path.stem}_stats_") as folder:
        summary = None
        csv_path = path
        outcome = "输入为 CSV（无产测结果）"
        if magic == b"VIB2":
            summary = WaveformSummary()
            csv_path = folder / "samples.csv"
            with (
                path.open("rb") as src,
                csv_path.open("w", encoding="utf-8", newline="") as dst,
            ):
                status, count = convert(src, dst, summary=summary)
            outcome = f"转换成功：{count} 条；产测结果：{status}"
            if not count:
                return folder, outcome + "；无采样可分析"
        groups, levels = generate_report(csv_path, folder, figures, summary=summary)
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
