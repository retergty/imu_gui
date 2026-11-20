"""Decode the factory_test CLI status emitted by PX4's FactoryTest.cpp."""

import re


STATES = {
    'IDLE': '空闲', 'PRECHECK': '正在进行启动检查', 'RUNNING': '测试进行中',
    'COMPLETE': '测试已完成', 'ABORTED': '测试已中止',
}
REASONS = {
    'another command is active': '飞控正在执行另一项产测',
    'command implementation is unavailable': '飞控不支持此测试',
    'system state missing, stale or timestamp in future': '飞控系统状态缺失或时间异常',
    'system is not disarmed': '飞控未处于上锁状态',
    'kill, lockdown or termination is active': '急停、锁定或飞行终止保护已生效',
    'ESC calibration is active': '电调正在校准',
    'sensor or RC calibration is active': '传感器或遥控器正在校准',
    'user abort': '用户请求中止',
    'module stopped': '产测模块已停止',
    'command failed': '飞控报告测试失败',
}
STAGES = {
    'opening log': '正在创建波形文件', 'waiting for output permit': '正在等待电机输出许可',
    'checking IMU': '正在检查 IMU', 'initial stop wait': '正在等待电机停稳',
    'baseline': '正在采集初始基线', 'ramp': '正在调整电机输出',
    'waiting for stable RPM': '正在等待转速稳定', 'sampling IMU': '正在采集 IMU 波形',
    'stop wait': '正在等待电机停稳', 'final reference': '正在采集停转后的参考数据',
    'finalizing log': '正在完成波形写入', 'complete': '采样已完成', 'failed': '采样失败',
}
LOG_STATES = {
    'idle': '尚未开始', 'opening': '正在创建', 'active': '正在写入',
    'closing': '正在收尾', 'closed': '文件已关闭，但记录未完整保存', 'failed': '保存失败',
}

# PX4 may color its output and MAVLink can split a line or a prompt at any byte.
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
PROMPT = re.compile(rf'^(?:\r|{ANSI.pattern})*(?:nsh|pxh)> ')
PREFIX = re.compile(r'^(?:INFO|WARN|ERROR|ERR|DEBUG)\s+(?:\[factory_test\]\s*)?')
HEADER = re.compile(r'run: (\d+), command: (\w+), state: (\w+)')
PROGRESS = re.compile(r'progress: (\d+)%, reason: (.+)')
OUTPUT = re.compile(r'output control: (yes|no); session: \d+; permit: \d+; reason: \d+')
STAGE = re.compile(r'vibration(?: point (\d+))?: (.+)')
LOG = re.compile(r'log: (\w+); handle: \d+; run: (\d+); terminal: (yes|no); complete: (yes|no)')
LOG_PATH = re.compile(r'log path: (.*); accepted/written: (\d+)/(\d+); errno: (-?\d+)')


class StatusStream:
    def __init__(self):
        self.buffer = ''
        self.lines = []

    def feed(self, text):
        """Yield messages and a complete status snapshot at the next shell prompt."""
        self.buffer += text
        while self.buffer:
            prompt = PROMPT.match(self.buffer)
            if prompt:
                self.buffer = self.buffer[prompt.end():]
                if self.lines:
                    yield 'status', self.lines
                    self.lines = []
                continue
            raw, newline, rest = self.buffer.partition('\n')
            if not newline:
                if len(self.buffer) > 4096:
                    self.buffer = ''
                break
            self.buffer = rest
            line = PREFIX.sub('', ANSI.sub('', raw).strip())
            if HEADER.fullmatch(line):
                self.lines = [line]
            elif self.lines and len(self.lines) < 20:
                self.lines.append(line)
            elif line:
                yield 'line', line


def describe_status(lines):
    run, command, state = HEADER.fullmatch(lines[0]).groups()
    name = {'motor_vibration': '振动测试', 'none': '产测模块'}.get(command, '其他产测')
    parts = [f'{name} · {STATES.get(state, "状态未知")}']
    if command != 'none':
        parts.append(f'任务编号 {run}')
    log_index = None
    for i, line in enumerate(lines[1:], 1):
        if match := PROGRESS.fullmatch(line):
            progress, reason = match.groups()
            parts.append(f'进度 {progress}%')
            if reason != 'none':
                parts.append(f'原因：{REASONS.get(reason, "飞控未提供可识别的原因")}')
        elif command == 'motor_vibration' and (match := OUTPUT.fullmatch(line)):
            parts.append('产测已接管电机输出' if match[1] == 'yes' else '产测未接管电机输出')
        elif command == 'motor_vibration' and (match := STAGE.fullmatch(line)):
            point, stage = match.groups()
            if stage in STAGES and state in {'PRECHECK', 'RUNNING'}:
                parts.append((f'第 {point} 测点：' if point else '') + STAGES[stage])
        elif (match := LOG.fullmatch(line)) and command == 'motor_vibration':
            log_state, log_run, terminal, complete = match.groups()
            if log_run == run:
                log_index = i
                description = ('已保存到飞控 SD 卡' if complete == 'yes' else
                               '保存失败或记录不完整' if terminal == 'yes' else
                               LOG_STATES.get(log_state, '状态未知'))
                parts.append(f'波形：{description}')
        elif log_index == i - 1 and (match := LOG_PATH.fullmatch(line)):
            path, submitted, written, error = match.groups()
            if path:
                parts.append(f'文件：{path.rsplit("/", 1)[-1]}')
            parts.append(f'已写入 {written}/{submitted} 字节')
            if error != '0':
                parts.append(f'存储错误码 {error}')
    return command, state, ' · '.join(parts)
