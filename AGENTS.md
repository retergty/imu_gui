# 项目 Python 环境

- 在本项目运行任何 Python 脚本、测试、模块或安装依赖时，使用项目根目录的 `.venv/bin/python`，不要调用系统 `python`、`python3` 或裸 `pip`。
- 在项目根目录可直接运行 `.venv/bin/python 脚本.py`、`.venv/bin/python -m pip ...`；从其他工作目录调用时使用项目解释器的绝对路径。不要依赖终端已经激活虚拟环境。
- Python 代码启动 Python 子进程时使用 `sys.executable`，使子进程沿用项目环境。如果项目解释器不存在或不可运行，先报告，不自动回退到系统 Python。
