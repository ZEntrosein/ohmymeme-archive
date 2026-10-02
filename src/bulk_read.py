"""批量读取保护。

虚拟盘上的一次文件读取可能被转换成云端请求。保护开启后，所有接入此
模块的批量原件读取会串行执行，并按用户设置的批量大小和时间暂停，降低
短时间内集中访问云盘的概率。默认关闭，避免影响普通本地磁盘的读取速度。
"""

import threading
import time
from contextlib import contextmanager

# 默认值保持原行为；设置页允许在安全范围内调整。
BULK_READ_BATCH_SIZE = 8
BULK_READ_PAUSE_SECONDS = 1.0
MIN_BATCH_SIZE = 1
MAX_BATCH_SIZE = 1000
MIN_PAUSE_SECONDS = 0.0
MAX_PAUSE_SECONDS = 60.0

PRIORITY_CRITICAL = 0
PRIORITY_FOREGROUND = 1
PRIORITY_BACKGROUND = 2
_PRIORITY_NAMES = {
    "critical": PRIORITY_CRITICAL,
    "foreground": PRIORITY_FOREGROUND,
    "background": PRIORITY_BACKGROUND,
}


def clamp_batch_size(value) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = BULK_READ_BATCH_SIZE
    return max(MIN_BATCH_SIZE, min(value, MAX_BATCH_SIZE))


def clamp_pause_seconds(value) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = BULK_READ_PAUSE_SECONDS
    return max(MIN_PAUSE_SECONDS, min(value, MAX_PAUSE_SECONDS))


def limits() -> tuple[int, float]:
    """读取实时限流参数；非法配置回退并钳制到安全范围。"""
    try:
        from .config import get_config

        cfg = get_config()
        return (
            clamp_batch_size(cfg.get("bulk_read_batch_size", BULK_READ_BATCH_SIZE)),
            clamp_pause_seconds(
                cfg.get("bulk_read_pause_seconds", BULK_READ_PAUSE_SECONDS)
            ),
        )
    except Exception:
        return BULK_READ_BATCH_SIZE, BULK_READ_PAUSE_SECONDS


class _BulkReadGate:
    def __init__(self):
        self._condition = threading.Condition(threading.RLock())
        self._local = threading.local()
        self._count = 0
        self._active = False
        self._waiting = [0, 0, 0]
        self._cooldown_until = 0.0

    @staticmethod
    def _priority(value) -> int:
        if isinstance(value, str):
            return _PRIORITY_NAMES.get(value.lower(), PRIORITY_FOREGROUND)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return PRIORITY_FOREGROUND
        return max(PRIORITY_CRITICAL, min(value, PRIORITY_BACKGROUND))

    def _can_enter(self, priority: int, now: float) -> bool:
        if self._active:
            return False
        if any(self._waiting[level] for level in range(priority)):
            return False
        # 用户明确触发的保存、上传、复制等关键操作可以穿过后台冷却期；
        # 它们仍会计入批次，并把后续前台/后台读取的冷却期向后延长。
        return priority == PRIORITY_CRITICAL or now >= self._cooldown_until

    @contextmanager
    def operation(self, priority="foreground"):
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield
            finally:
                self._local.depth = depth
            return
        priority = self._priority(priority)
        with self._condition:
            self._waiting[priority] += 1
            try:
                while not self._can_enter(priority, time.monotonic()):
                    timeout = None
                    if priority != PRIORITY_CRITICAL and not self._active:
                        remaining = self._cooldown_until - time.monotonic()
                        if remaining > 0:
                            timeout = remaining
                    self._condition.wait(timeout=timeout)
                self._active = True
            finally:
                self._waiting[priority] -= 1
        self._local.depth = 1
        try:
            yield
        finally:
            with self._condition:
                # 失败的远端读取同样可能触发了请求，必须计入批次，避免
                # 网络异常时连续重试绕过限速保护。
                self._count += 1
                batch_size, pause_seconds = limits()
                if self._count >= batch_size:
                    self._count = 0
                    if pause_seconds > 0:
                        self._cooldown_until = max(
                            self._cooldown_until,
                            time.monotonic() + pause_seconds,
                        )
                self._local.depth = 0
                self._active = False
                self._condition.notify_all()


_GATE = _BulkReadGate()


def enabled() -> bool:
    """读取当前配置；配置加载失败时保持关闭，不能阻断应用。"""
    try:
        from .config import get_config

        return get_config().get("bulk_read_protection", False) is True
    except Exception:
        return False


@contextmanager
def guarded_read(priority="foreground"):
    """保护一次可能读取挂载盘原件的操作，并按交互优先级排队。"""
    if not enabled():
        yield
        return
    with _GATE.operation(priority):
        yield
