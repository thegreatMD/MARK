import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


PERMISSION_DEFS: Dict[str, Dict[str, str]] = {
    "camera": {
        "label": "Camera access",
        "description": "Access your webcam for presence detection, brightness monitoring, or visual input.",
        "tts_prompt": "Mark wants to access your camera. Should I allow it? Say yes or no.",
    },
    "microphone": {
        "label": "Microphone access",
        "description": "Listen to your voice for speech recognition and voice commands.",
        "tts_prompt": "Mark wants to use your microphone to listen for voice commands. Should I allow it? Say yes or no.",
    },
    "screen_capture": {
        "label": "Screen capture and OCR",
        "description": "Take screenshots of your desktop to read text, active windows, and visible content.",
        "tts_prompt": "Mark wants to capture screenshots and read text from your screen. Should I allow it? Say yes or no.",
    },
    "desktop_control": {
        "label": "Desktop automation (mouse/keyboard)",
        "description": "Move the mouse, click, and simulate keyboard input on your desktop using pyautogui.",
        "tts_prompt": "Mark wants to control your mouse and keyboard for desktop automation. Should I allow it? Say yes or no.",
    },
    "browser_automation": {
        "label": "Browser automation",
        "description": "Launch a headless browser, navigate websites, and fill forms automatically using Playwright.",
        "tts_prompt": "Mark wants to run browser automation tasks. Should I allow it? Say yes or no.",
    },
    "clipboard_access": {
        "label": "Clipboard access",
        "description": "Read the text currently stored in your system clipboard.",
        "tts_prompt": "Mark wants to read from your clipboard. Should I allow it? Say yes or no.",
    },
}

VALID_STATUSES = {"granted", "denied", "unknown"}


class PermissionManager:
    """Persistent, prompt-based permission manager for hardware and automation access.

    Permission storage is a simple JSON file saved next to the assistant.
    Status values are:
      - "granted"  -> user explicitly approved this permission
      - "denied"   -> user explicitly rejected this permission
      - "unknown"  -> never asked, or grant was revoked; a prompt will be shown
    """

    def __init__(
        self,
        storage_path: Path,
        speak_fn: Optional[Callable[[str], None]] = None,
        listen_fn: Optional[Callable[[], str]] = None,
        dashboard_event_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.storage_path = Path(storage_path)
        self.speak_fn = speak_fn
        self.listen_fn = listen_fn
        self.dashboard_event_fn = dashboard_event_fn
        self._lock = threading.Lock()
        self._grants: Dict[str, str] = {}
        self._pending_requests: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------
    def _load(self) -> None:
        try:
            if self.storage_path.is_file():
                raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for key, value in raw.items():
                        if isinstance(value, str) and value in VALID_STATUSES:
                            self._grants[key] = value
        except (OSError, json.JSONDecodeError):
            pass

    def _save(self) -> None:
        try:
            self.storage_path.write_text(
                json.dumps(self._grants, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def is_defined(self, permission: str) -> bool:
        return permission in PERMISSION_DEFS

    def status(self, permission: str) -> str:
        if not self.is_defined(permission):
            return "denied"
        return self._grants.get(permission, "unknown")

    def is_granted(self, permission: str) -> bool:
        return self.status(permission) == "granted"

    def all_statuses(self) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        for key, meta in PERMISSION_DEFS.items():
            result[key] = {
                "label": meta["label"],
                "description": meta["description"],
                "status": self.status(key),
            }
        return result

    # ------------------------------------------------------------------
    # Mutations (for dashboard/API)
    # ------------------------------------------------------------------
    def set_status(self, permission: str, status: str) -> bool:
        if not self.is_defined(permission) or status not in VALID_STATUSES:
            return False
        with self._lock:
            self._grants[permission] = status
            self._save()
        self._emit(f"Permission {PERMISSION_DEFS[permission]['label']}: {status}")
        self._resolve_pending(permission, status == "granted")
        return True

    def reset_all(self) -> None:
        with self._lock:
            self._grants.clear()
            self._save()
        self._emit("All permissions reset — user will be prompted again.")

    # ------------------------------------------------------------------
    # Guard: request-on-use
    # ------------------------------------------------------------------
    def request(
        self,
        permission: str,
        blocking: bool = True,
        timeout_seconds: int = 60,
    ) -> bool:
        """Check a permission, and if it is unknown, prompt the user to approve.

        Returns True if granted (either previously or during this call).
        If blocking=False and status is unknown, returns False immediately but
        exposes the pending request so the dashboard UI can respond.
        """
        if not self.is_defined(permission):
            return False
        current = self.status(permission)
        if current == "granted":
            return True
        if current == "denied":
            self._emit(f"Permission denied: {PERMISSION_DEFS[permission]['label']}")
            return False

        if not blocking:
            self._queue_pending(permission)
            return False

        approved = self._prompt_user_interactive(permission, timeout_seconds)
        with self._lock:
            self._grants[permission] = "granted" if approved else "denied"
            self._save()
        return approved

    def require(self, permission: str, timeout_seconds: int = 60) -> None:
        """Raise PermissionError if the user does not grant the permission."""
        if not self.request(permission, blocking=True, timeout_seconds=timeout_seconds):
            label = PERMISSION_DEFS.get(permission, {}).get("label", permission)
            raise PermissionError(f"Permission not granted: {label}")

    # ------------------------------------------------------------------
    # Pending requests (for non-blocking / dashboard resolution)
    # ------------------------------------------------------------------
    def _queue_pending(self, permission: str) -> None:
        with self._lock:
            already = permission in self._pending_requests
            self._pending_requests[permission] = {
                "queued_at": _now_iso(),
                "permission": permission,
            }
        if already:
            return
        meta = PERMISSION_DEFS[permission]
        self._emit(
            f"Permission pending: {meta['label']}. "
            "Approve or deny from the dashboard permissions panel."
        )

    def pending_requests(self) -> List[Dict[str, Any]]:
        return [
            {
                "permission": key,
                "label": PERMISSION_DEFS[key]["label"],
                "description": PERMISSION_DEFS[key]["description"],
                "queued_at": val.get("queued_at"),
            }
            for key, val in sorted(self._pending_requests.items(), key=lambda kv: kv[1].get("queued_at", ""))
        ]

    def _resolve_pending(self, permission: str, granted: bool) -> None:
        with self._lock:
            self._pending_requests.pop(permission, None)

    # ------------------------------------------------------------------
    # Interactive prompt helpers
    # ------------------------------------------------------------------
    def _prompt_user_interactive(self, permission: str, timeout_seconds: int) -> bool:
        meta = PERMISSION_DEFS[permission]
        prompt_tts = meta["tts_prompt"]
        prompt_console = (
            f"\n[PERMISSION REQUEST] {meta['label']}\n"
            f"  {meta['description']}\n"
            f"  Allow? [y/N] (or say 'yes'/'no' if voice is enabled): "
        )

        self._emit(f"Permission request: {meta['label']} (awaiting user approval)")

        if self.speak_fn:
            try:
                self.speak_fn(prompt_tts)
            except Exception:
                pass

        voice_answer: Optional[str] = None
        if self.listen_fn:
            try:
                heard = (self.listen_fn() or "").strip().lower()
                if heard:
                    voice_answer = heard
            except Exception:
                pass

        if voice_answer:
            if _is_yes(voice_answer):
                self._emit(f"User approved {meta['label']} by voice.")
                return True
            if _is_no(voice_answer):
                self._emit(f"User denied {meta['label']} by voice.")
                return False

        try:
            interactive = sys.stdin is not None and sys.stdin.isatty()
        except Exception:
            interactive = False
        if not interactive:
            self._queue_pending(permission)
            self._emit(f"{meta['label']} needs approval on the overlay ACCESS panel.")
            return False

        try:
            text = input(prompt_console).strip().lower()
        except (EOFError, OSError):
            text = ""

        if _is_yes(text):
            self._emit(f"User approved {meta['label']}.")
            return True
        self._emit(f"User denied {meta['label']}.")
        return False

    def _emit(self, message: str) -> None:
        try:
            if self.dashboard_event_fn:
                self.dashboard_event_fn(message)
        except Exception:
            pass
        try:
            print(message)
        except Exception:
            pass


def _is_yes(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(word in t for word in ["yes", "yep", "sure", "okay", "ok", "y", "confirm", "allow", "grant", "do it", "proceed"])


def _is_no(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(word in t for word in ["no", "nope", "n", "deny", "reject", "cancel", "stop", "decline"])


def _now_iso() -> str:
    try:
        from datetime import datetime
        return datetime.now().isoformat(timespec="seconds")
    except Exception:
        return ""
