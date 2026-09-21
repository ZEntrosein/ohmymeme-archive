"""进程内内容库变更通知。"""

import threading
import weakref

_LOCK = threading.Lock()
_CALLBACK = None


def set_change_callback(callback):
    """注册唯一回调；绑定方法使用弱引用，不阻止 WebUI 被释放。"""
    global _CALLBACK
    with _LOCK:
        if callback is None:
            _CALLBACK = None
            return
        try:
            _CALLBACK = weakref.WeakMethod(callback)
        except TypeError:
            _CALLBACK = weakref.ref(callback)


def notify_changed(kind="content") -> bool:
    """通知后台入库完成；没有活动主窗口时安全忽略。"""
    global _CALLBACK
    with _LOCK:
        reference = _CALLBACK
        callback = reference() if reference is not None else None
        if reference is not None and callback is None:
            _CALLBACK = None
    if callback is None:
        return False
    try:
        callback("structure" if kind == "structure" else "content")
        return True
    except Exception:
        return False
