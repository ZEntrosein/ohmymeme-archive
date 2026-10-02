"""Windows 原生文件拖拽（DoDragDrop + CF_HDROP）

WebView2 的 HTML5 拖拽拖出到外部应用不会生成 CF_HDROP（http 源限制），
QQ/微信只接受真实本地文件。本模块用 WinForms DoDragDrop 构造文件拖放，
让表情卡片能以原生文件形式拖到外部应用。

仅在 Windows + pythonnet 可用时生效，其余平台返回 False。
"""

import ctypes
import os
import platform
import threading
from ctypes import wintypes

_lock = threading.Lock()


def _import_wf():
    """惰性导入 pythonnet 与 WinForms，失败返回 None"""
    try:
        import clr

        clr.AddReference("System.Windows.Forms")
        import System.Windows.Forms as WinForms

        return WinForms
    except Exception:
        return None


def _cursor_target():
    """返回鼠标所在顶层窗口、进程名和坐标。"""
    user32 = ctypes.windll.user32
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return None
    window = user32.WindowFromPoint(point)
    root = user32.GetAncestor(window, 2) if window else 0
    if not root:
        return None
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(root, ctypes.byref(process_id))
    try:
        from System.Diagnostics import Process

        process_name = str(Process.GetProcessById(int(process_id.value)).ProcessName)
    except Exception:
        process_name = ""
    return {
        "window_handle": int(root),
        "process_name": process_name,
        "x": int(point.x),
        "y": int(point.y),
    }


def start_native_drag(path):
    """在宿主窗口 UI 线程执行原生文件拖拽，返回是否成功启动

    :param path: 本地文件绝对路径
    """
    if platform.system() != "Windows":
        return False
    if not path or not os.path.isfile(path):
        return False
    try:
        import webview

        if not webview.windows:
            return False
        form = webview.windows[0].native
        if form is None:
            return False
    except Exception:
        return False

    WinForms = _import_wf()
    if WinForms is None:
        return False

    from System import Action, Array, String

    data = WinForms.DataObject(WinForms.DataFormats.FileDrop, Array[String]([path]))
    result = {}

    def _run():
        try:
            effect = form.DoDragDrop(data, WinForms.DragDropEffects.Copy)
            result["val"] = str(effect)
            # pythonnet 中 DragDropEffects.None 与 Python None 冲突，用字符串判断取消
            result["cancelled"] = str(effect).lower() == "none"
        except Exception as e:
            result["err"] = repr(e)

    try:
        with _lock:
            form.Invoke(Action(_run))
    except Exception:
        return False
    if "err" in result:
        return False
    return not result.get("cancelled", False)


def start_meme_drag(path, intercept_qq=False):
    """拖出本地图片；需要时拦截 QQ 落点交由 NapCat 发送。"""
    if not intercept_qq:
        ok = bool(start_native_drag(path))
        return {"ok": ok, "file_delivered": ok}
    if platform.system() != "Windows":
        return {"ok": False, "error": "QQ 表情拖拽发送目前仅支持 Windows"}
    if not path or not os.path.isfile(path):
        return {"ok": False, "error": "本地文件不存在"}
    try:
        import webview

        if not webview.windows or webview.windows[0].native is None:
            return {"ok": False, "error": "主窗口尚未就绪"}
        form = webview.windows[0].native
    except Exception:
        return {"ok": False, "error": "无法访问主窗口"}

    WinForms = _import_wf()
    if WinForms is None:
        return {"ok": False, "error": "缺少 Windows 原生拖拽组件"}

    from System import Action, Array, String

    data = WinForms.DataObject(WinForms.DataFormats.FileDrop, Array[String]([path]))
    result = {"qq_target": None, "cancelled": False}

    def _query_continue(_sender, event):
        try:
            action = str(event.Action).lower()
            if event.EscapePressed or action == "cancel":
                result["cancelled"] = True
                return
            if action != "drop":
                return
            target = _cursor_target()
            if target and str(target.get("process_name") or "").casefold() == "qq":
                result["qq_target"] = target
                event.Action = WinForms.DragAction.Cancel
        except Exception:
            pass

    def _run():
        try:
            form.QueryContinueDrag += _query_continue
            effect = form.DoDragDrop(data, WinForms.DragDropEffects.Copy)
            result["effect"] = str(effect)
        except Exception as exc:
            result["error"] = repr(exc)
        finally:
            try:
                form.QueryContinueDrag -= _query_continue
            except Exception:
                pass

    try:
        with _lock:
            form.Invoke(Action(_run))
    except Exception:
        return {"ok": False, "error": "无法启动原生拖拽"}
    if result.get("error"):
        return {"ok": False, "error": "原生拖拽失败"}
    if result.get("qq_target"):
        return {"ok": True, "qq_target": result["qq_target"]}
    if result.get("cancelled") or str(result.get("effect") or "").lower() == "none":
        return {"ok": False, "cancelled": True}
    return {"ok": True, "file_delivered": True}


def start_chat_record_target_drag(record_id):
    """启动不向目标投递内容的聊天记录拖拽，并返回松手位置的顶层窗口。

    QQ 不需要接收任何文本或文件；拖拽只作为用户明确选择目标窗口的手势。
    Esc 取消与鼠标正常松手通过 QueryContinueDrag 区分。
    """
    if platform.system() != "Windows":
        return {"ok": False, "error": "聊天记录拖拽发送目前仅支持 Windows"}
    try:
        import webview

        if not webview.windows:
            return {"ok": False, "error": "主窗口尚未就绪"}
        form = webview.windows[0].native
        if form is None:
            return {"ok": False, "error": "主窗口尚未就绪"}
    except Exception:
        return {"ok": False, "error": "无法访问主窗口"}

    WinForms = _import_wf()
    if WinForms is None:
        return {"ok": False, "error": "缺少 Windows 原生拖拽组件"}

    from System import Action

    data = WinForms.DataObject()
    data.SetData("OhMyMeme.ChatRecord", False, str(record_id))
    result = {"released": False, "cancelled": False}

    def _query_continue(_sender, event):
        try:
            action = str(event.Action).lower()
            if event.EscapePressed or action == "cancel":
                result["cancelled"] = True
            elif action == "drop":
                result["released"] = True
        except Exception:
            pass

    def _run():
        try:
            form.QueryContinueDrag += _query_continue
            form.DoDragDrop(data, WinForms.DragDropEffects.Copy)
        except Exception as exc:
            result["error"] = repr(exc)
        finally:
            try:
                form.QueryContinueDrag -= _query_continue
            except Exception:
                pass

    try:
        with _lock:
            form.Invoke(Action(_run))
    except Exception:
        return {"ok": False, "error": "无法启动原生拖拽"}
    if result.get("error"):
        return {"ok": False, "error": "原生拖拽失败"}
    if result.get("cancelled") or not result.get("released"):
        return {"ok": False, "cancelled": True}

    target = _cursor_target()
    if not target:
        return {"ok": False, "error": "无法读取拖拽位置"}
    return {"ok": True, **target}
