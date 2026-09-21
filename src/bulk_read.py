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
        self._lock = threading.RLock()
        self._local = threading.local()
        self._count = 0

    @contextmanager
    def operation(self):
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield
            finally:
                self._local.depth = depth
            return
        self._lock.acquire()
        self._local.depth = 1
        try:
            yield
        finally:
            try:
                # 失败的远端读取同样可能触发了请求，必须计入批次，避免
                # 网络异常时连续重试绕过限速保护。
                self._count += 1
                batch_size, pause_seconds = limits()
                if self._count >= batch_size:
                    self._count = 0
                    # 保持闸门占用到暂停结束，确保并发 worker 不会在暂停期间
                    # 立即补上一批请求。
                    if pause_seconds > 0:
                        time.sleep(pause_seconds)
            finally:
                self._local.depth = 0
                self._lock.release()


_GATE = _BulkReadGate()


def enabled() -> bool:
    """读取当前配置；配置加载失败时保持关闭，不能阻断应用。"""
    try:
        from .config import get_config

        return get_config().get("bulk_read_protection", False) is True
    except Exception:
        return False


@contextmanager
def guarded_read():
    """保护一次可能读取挂载盘原件的操作。"""
    if not enabled():
        yield
        return
    with _GATE.operation():
        yield
