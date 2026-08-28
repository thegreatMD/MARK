"""Always-on-top J.A.R.V.I.S. overlay popup.

Talks to the local Mark dashboard at /hud. Tries pywebview first, then Edge/Chrome app mode.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen


class HudBridge:
    """JS-callable window controls. window.close() does nothing on a frameless pywebview."""

    def __init__(self) -> None:
        self.window = None

    def close_window(self) -> str:
        if self.window is not None:
            self.window.destroy()
        return "closed"

    def minimize_window(self) -> str:
        if self.window is not None:
            self.window.minimize()
        return "minimized"

    def collapse_window(self) -> str:
        if self.window is not None:
            self.window.resize(240, 260)
        return "collapsed"

    def expand_window(self) -> str:
        if self.window is not None:
            self.window.resize(460, 780)
        return "expanded"


def wait_for_server(url: str, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.4)
    return False


def screen_size() -> tuple[int, int]:
    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


def pin_hwnd_topmost(hwnd: int) -> None:
    HWND_TOPMOST = -1
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_SHOWWINDOW = 0x0040
    ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)


def launch_webview(url: str, width: int, height: int, x: int, y: int) -> None:
    import webview

    bridge = HudBridge()
    window = webview.create_window(
        "J.A.R.V.I.S.",
        url,
        width=width,
        height=height,
        x=x,
        y=y,
        frameless=True,
        on_top=True,
        easy_drag=False,
        shadow=True,
        background_color="#02060d",
        resizable=True,
        js_api=bridge,
    )
    bridge.window = window
    webview.start()


def _browser_exe() -> str | None:
    candidates = [
        shutil.which("msedge"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def launch_edge_app(url: str, width: int, height: int, x: int, y: int) -> None:
    exe = _browser_exe()
    if not exe:
        raise FileNotFoundError("Edge/Chrome not found for HUD overlay")
    subprocess.Popen(
        [
            exe,
            f"--app={url}",
            f"--window-size={width},{height}",
            f"--window-position={x},{y}",
            "--new-window",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.8)
    user32 = ctypes.windll.user32
    found: list[int] = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if "J.A.R.V.I.S" in title or "JARVIS" in title:
                found.append(int(hwnd))
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    for hwnd in found:
        pin_hwnd_topmost(hwnd)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def already_running() -> bool:
    lock = Path(__file__).with_name(".hud.lock")
    if lock.exists():
        try:
            pid = int(lock.read_text(encoding="utf-8").strip() or "0")
            if pid_alive(pid) and pid != os.getpid():
                return True
        except Exception:
            pass
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return False


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    hud_url = f"http://127.0.0.1:{port}/hud"
    if not wait_for_server(f"http://127.0.0.1:{port}/api/state"):
        print("HUD: dashboard not ready")
        return
    if already_running():
        return
    sw, sh = screen_size()
    width, height = 460, 780
    x = max(16, sw - width - 20)
    y = max(16, sh - height - 70)
    try:
        launch_webview(hud_url, width, height, x, y)
    except Exception as exc:
        print("HUD webview unavailable, using browser app window:", exc)
        launch_edge_app(hud_url, width, height, x, y)


if __name__ == "__main__":
    try:
        main()
    finally:
        lock = Path(__file__).with_name(".hud.lock")
        try:
            if lock.exists() and lock.read_text(encoding="utf-8").strip() == str(os.getpid()):
                lock.unlink()
        except Exception:
            pass
