"""Global hotkeys via Win32 RegisterHotKey on a dedicated message-pump thread.

Unlike low-level keyboard hooks, RegisterHotKey is message-based, so it isn't
subject to LowLevelHooksTimeout and keeps working across session lock/unlock
and display sleep.
"""
import ctypes
import threading
from ctypes import wintypes

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_OEM_PLUS = 0xBB   # =/+ key
VK_OEM_MINUS = 0xBD  # -/_ key

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
    ]


class HotkeyManager:
    def __init__(self):
        self._bindings = []
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()

    def add(self, modifiers, vk, callback, norepeat=True):
        mods = (modifiers | MOD_NOREPEAT) if norepeat else modifiers
        self._bindings.append((mods, vk, callback))

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def stop(self):
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _run(self):
        self._thread_id = _kernel32.GetCurrentThreadId()
        callbacks = {}
        for i, (mods, vk, cb) in enumerate(self._bindings, start=1):
            if _user32.RegisterHotKey(None, i, mods, vk):
                callbacks[i] = cb
            else:
                err = ctypes.get_last_error()
                print(f"RegisterHotKey failed for id={i} vk={vk:#x} mods={mods:#x} (err={err})")
        # Force the thread to have a message queue before signaling ready.
        msg = _MSG()
        _user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 0)
        self._ready.set()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    cb = callbacks.get(msg.wParam)
                    if cb:
                        try:
                            cb()
                        except Exception as e:
                            print(f"Hotkey callback error: {e}")
        finally:
            for hk_id in callbacks:
                _user32.UnregisterHotKey(None, hk_id)
