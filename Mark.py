import os
import csv
import ctypes
import threading
import subprocess
import sys
import hashlib
import ipaddress
import json
import re   
import socket
import time
import requests
import speech_recognition as sr
import pyttsx3
from bs4 import BeautifulSoup
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv
from difflib import SequenceMatcher

from dashboard import DashboardServer
from permissions import PermissionManager, PERMISSION_DEFS
from command_learner import CommandLearner
import pathlib
from typing import Any

# These are the environment keys used by the assistant.
CONFIG_KEYS = [
    "N8N_WEBHOOK_URL",  # n8n webhook entrypoint for automation workflows
    "GOOGLE_SHEET_ID",  # target Google Sheet for lead saving
    "GOOGLE_DRIVE_FOLDER_ID",  # target Google Drive folder for file storage
    "GOOGLE_CREDENTIALS_PATH",  # path to Google service account credentials
    "SPEECH_LANGUAGE",  # microphone recognition language
    "MARK_CHAT_WEBHOOK_URL",  # optional n8n/AI webhook for Chrome companion chat
    "MARK_API_TOKEN",  # optional shared token for the Chrome companion
    "MARK_CAPTURE_COLLECTION",  # name for browser/screen capture batches
    "MARK_CAPTURE_MODE",  # capture frequency policy (manual by default)
    "ASK_PERMISSION_BEFORE_USE",  # when true, camera/mic/screen/etc require explicit prompt before use
]


def load_config() -> Dict[str, Optional[str]]:
    """Load environment variables from a local .env file."""
    env_path = Path(__file__).with_name(".env")
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    extra = {
        "AUTONOMY_LEVEL": os.getenv("AUTONOMY_LEVEL"),
        "ALLOWLIST": os.getenv("ALLOWLIST"),
    }
    base = {key: os.getenv(key) for key in CONFIG_KEYS}
    base.update(extra)
    return base


class MarkAssistant:
    def __init__(self, config: Dict[str, Optional[str]]):
        self.config = config
        self.speech_language = config.get("SPEECH_LANGUAGE") or "en-US"
        self.engine = self._initialize_speech_engine()
        self._tts_lock = threading.Lock()
        self.n8n_webhook_url = config.get("N8N_WEBHOOK_URL")
        self.mark_chat_webhook_url = config.get("MARK_CHAT_WEBHOOK_URL")
        self.mark_api_token = config.get("MARK_API_TOKEN")
        self.capture_collection = config.get("MARK_CAPTURE_COLLECTION") or "Mark"
        self.sheet_id = config.get("GOOGLE_SHEET_ID")
        self.drive_folder_id = config.get("GOOGLE_DRIVE_FOLDER_ID")
        self.google_credentials = config.get("GOOGLE_CREDENTIALS_PATH")
        self.gspread_client = None
        self.drive_service = None
        self.local_leads_path = Path(__file__).with_name("leads.csv")
        self.knowledge_dir = Path(__file__).with_name("knowledge")
        self.knowledge_dir.mkdir(exist_ok=True)
        self.ask_permission_before_use = str(config.get("ASK_PERMISSION_BEFORE_USE") or "true").lower() not in {"0", "false", "no", "off", "disabled"}
        self.permissions = PermissionManager(
            storage_path=Path(__file__).with_name("permissions.json"),
            speak_fn=self.speak,
            listen_fn=self._permission_listen_shim,
            dashboard_event_fn=self._dashboard_event,
        )
        self.learner = CommandLearner(
            storage_path=Path(__file__).with_name("learned_commands.jsonl"),
            event_fn=self._dashboard_event,
        )
        self.dashboard = DashboardServer(
            port=8080,
            command_handler=self.handle_dashboard_command,
            chat_handler=self.handle_extension_chat,
            learn_handler=self.learn_link,
            self_test_handler=self.run_self_test,
            permission_manager=self.permissions,
            permission_set_handler=self._set_permission_handler,
            command_learner=self.learner,
        )
        self.dashboard.start()
        self.dashboard.refresh_permissions_state()
        self._refresh_learner_dashboard_state()
        self.is_listening = True
        self.dashboard.update_state(status="Listening", last_action="Ready", listening=True, assistant_voice="Systems online.")
        self._stop_requested = False
        self.commands_file = Path(__file__).with_name("commands.json")
        self.commands = self._load_command_phrases()
        self.conversation_history = []
        self.intent_confidence_threshold = 0.65
        self.last_intent = None
        self.last_query = None
        self.autonomy_level = (config.get("AUTONOMY_LEVEL") or "manual").lower()
        allow_raw = config.get("ALLOWLIST") or ""
        self.allowlist = set([s.strip() for s in allow_raw.split(",") if s.strip()])
        self.action_log_path = Path(__file__).with_name("action_log.csv")
        self._set_persona_ironman()
        self._init_automation_tools()
        self._run_startup_checks()
        self._hud_process = None
        self._start_background_services()

    def _update_dashboard(self, **kwargs) -> None:
        """Update the dashboard state for visual feedback."""
        if self.dashboard:
            if "listening" not in kwargs and hasattr(self, "is_listening"):
                kwargs["listening"] = bool(self.is_listening)
            self.dashboard.update_state(**kwargs)

    def _dashboard_event(self, message: str) -> None:
        """Append a dashboard event message."""
        if self.dashboard:
            self.dashboard.add_event(message)
        try:
            if self.dashboard:
                self.dashboard.refresh_permissions_state()
        except Exception:
            pass

    def _request_permission(self, key: str, blocking: bool = True) -> bool:
        """Centralised permission gate.

        When `ASK_PERMISSION_BEFORE_USE` is disabled (rare), this simply returns True
        so the assistant runs as it did before the permission layer was added.
        Otherwise it defers to the permission manager for approval/prompting.
        """
        if not self.ask_permission_before_use:
            return True
        if not self.permissions.is_defined(key):
            return False
        if threading.current_thread() is not threading.main_thread():
            blocking = False
        return self.permissions.request(key, blocking=blocking)

    def _permission_listen_shim(self) -> str:
        """Wrapper used by the permission manager when prompting by voice.

        This short-circuits the normal `listen()` flow to avoid an infinite loop:
        asking the user for microphone permission should not itself require microphone
        permission. We still attempt SpeechRecognition but silently fall back to empty
        string if the mic is unavailable (so the user can answer via keyboard instead).
        """
        recognizer = sr.Recognizer()
        try:
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(source, phrase_time_limit=6, timeout=10)
            text = recognizer.recognize_google(audio, language=self.speech_language)
            return text or ""
        except Exception:
            return ""

    def _set_permission_handler(self, permission: str, status: str) -> bool:
        """Handler invoked when the dashboard user changes a permission via the API."""
        ok = self.permissions.set_status(permission, status)
        if ok:
            try:
                self.dashboard.refresh_permissions_state()
            except Exception:
                pass
            meta = PERMISSION_DEFS.get(permission, {})
            label = meta.get("label", permission)
            if status == "granted":
                self.speak(f"{label} permission granted.")
            elif status == "denied":
                self.speak(f"{label} permission denied.")
            else:
                self.speak(f"{label} permission reset; I will ask next time.")
        return ok

    def _refresh_learner_dashboard_state(self) -> None:
        try:
            stats = self.learner.stats() if self.learner else {}
            self._update_dashboard(
                learner_stats=stats,
                learner_top_patterns=self.learner.top_patterns(n=10) if self.learner else {},
            )
        except Exception:
            pass

    def _learner_boost_intent(self, query: str, base_intent: str, base_confidence: float) -> Tuple[str, float, Optional[str]]:
        """Combine rule-based intent detection with the learned model.

        If the learned model produces a strong enough signal, use it (even as an
        override when the fuzzy matcher is uncertain). Otherwise fall back to the
        base result. Returns (intent, confidence, source_note).
        """
        if not self.learner:
            return base_intent, base_confidence, None
        try:
            learned_intent, learned_conf, rationale = self.learner.infer(query)
        except Exception:
            return base_intent, base_confidence, None

        if learned_intent is None:
            return base_intent, base_confidence, None

        # If base matched (high conf), prefer base; but nudge confidence up if
        # learned agrees. If base didn't match (low conf) and learned is strong,
        # trust the learned signal.
        if learned_intent == base_intent:
            blended = min(1.0, 0.45 * base_confidence + 0.55 * learned_conf + 0.05)
            return base_intent, blended, rationale
        if base_confidence < self.intent_confidence_threshold and learned_conf >= max(0.55, base_confidence + 0.10):
            return learned_intent, learned_conf, rationale
        return base_intent, base_confidence, None

    def _record_command_to_learner(self, query: str, intent: str, confidence: float, acted: bool = True) -> None:
        """Enqueue a single executed (or explicitly routed) command for learning.

        Never blocks — enqueues a tiny record onto the learner worker queue.
        """
        if not self.learner or not query:
            return
        try:
            self.learner.record(query=query, intent=intent, confidence=confidence, acted=acted)
        except Exception:
            pass

    def _initialize_speech_engine(self):
        """Initialize Windows TTS when available without blocking Mark if it is not."""
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", 160)
            return engine
        except Exception as exc:
            print(f"Text-to-speech unavailable: {exc}")
            return None

    def _run_startup_checks(self, source: str = "Startup") -> Dict[str, Any]:
        """Run non-invasive readiness checks and publish the results to the dashboard."""
        credentials_path = Path(self.google_credentials) if self.google_credentials else None
        learner_stats = self.learner.stats() if getattr(self, "learner", None) else {}
        checks = [
            {"name": "Dashboard and chat API", "status": "ready"},
            {"name": "Voice output", "status": "ready" if self.engine else "unavailable"},
            {"name": "Voice input", "status": self._check_microphone()},
            {"name": "Screen OCR", "status": self._check_screen_ocr()},
            {"name": "Browser automation", "status": self._check_browser_automation()},
            {"name": "Chrome companion", "status": "ready" if Path(__file__).with_name("mark_chrome_extension").is_dir() else "missing"},
            {"name": "Link knowledge storage", "status": "ready" if self.knowledge_dir.is_dir() else "unavailable"},
            {"name": "AI chat workflow", "status": "ready" if self.mark_chat_webhook_url else "webhook not configured"},
            {
                "name": "Google Drive storage",
                "status": "ready" if self.drive_folder_id and credentials_path and credentials_path.is_file() else "credentials not configured",
            },
            {"name": "n8n automation", "status": "ready" if self.n8n_webhook_url else "webhook not configured"},
            {
                "name": "Lightweight command learner",
                "status": (
                    "ready"
                    if learner_stats
                    else "unavailable"
                ),
            },
        ]
        self._update_dashboard(startup_checks=checks, last_action=f"{source} self-test completed")
        self._refresh_learner_dashboard_state()
        ready_count = sum(check["status"] == "ready" for check in checks)
        message = f"{source} self-test: {ready_count}/{len(checks)} services ready"
        self._dashboard_event(message)
        return {
            "status": "ok",
            "message": message,
            "checks": checks,
            "ready_count": ready_count,
            "total_count": len(checks),
        }

    def _check_microphone(self) -> str:
        """Verify that SpeechRecognition can open a microphone without recording audio."""
        if not self._request_permission("microphone", blocking=False):
            return "permission required"
        try:
            with sr.Microphone() as source:
                if source.stream is None:
                    return "unavailable"
            return "ready"
        except Exception:
            return "unavailable"

    def _check_screen_ocr(self) -> str:
        """Verify screen capture and the OCR engine without storing any captured content."""
        if not self._has_screen_ocr:
            return "not installed"
        if not self._request_permission("screen_capture", blocking=False):
            return "permission required"
        try:
            self.ImageGrab.grab()
            self.pytesseract.get_tesseract_version()
            return "ready"
        except Exception:
            return "unavailable"

    def _check_browser_automation(self) -> str:
        """Check that Playwright and its Chromium executable are both available."""
        if not self._has_playwright:
            return "not installed"
        if not self._request_permission("browser_automation", blocking=False):
            return "permission required"
        try:
            with self.sync_playwright() as playwright:
                return "ready" if Path(playwright.chromium.executable_path).is_file() else "browser not installed"
        except Exception:
            return "unavailable"

    def run_self_test(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Allow dashboard users to re-run only non-invasive function readiness tests."""
        supplied_token = str(payload.get("token", ""))
        if self.mark_api_token and supplied_token != self.mark_api_token:
            return {"status": "error", "message": "Mark connection token is invalid."}
        return self._run_startup_checks("Manual")

    def handle_extension_chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle an explicit Chrome companion request without taking browser actions."""
        supplied_token = str(payload.get("token", ""))
        if self.mark_api_token and supplied_token != self.mark_api_token:
            return {"status": "error", "message": "Mark connection token is invalid."}

        message = str(payload.get("message", "")).strip()
        if not message:
            return {"status": "error", "message": "Please enter a message for Mark."}
        if len(message) > 4000:
            return {"status": "error", "message": "Message is too long (maximum 4,000 characters)."}

        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        page_url = str(context.get("url", ""))[:2048]
        page_title = str(context.get("title", ""))[:500]
        selected_text = str(context.get("selectedText", ""))[:12000]
        page_text = str(context.get("pageText", ""))[:12000]
        knowledge = self._find_relevant_knowledge(message)
        request_payload = {
            "message": message,
            "context": {
                "url": page_url,
                "title": page_title,
                "selectedText": selected_text,
                "pageText": page_text,
            },
            "knowledge": knowledge,
        }
        self._update_dashboard(current_query=message, last_action="Chrome companion message received")
        self._dashboard_event(f"Chrome companion request from: {page_title or page_url or 'unknown page'}")

        if self.mark_chat_webhook_url:
            try:
                response = requests.post(self.mark_chat_webhook_url, json=request_payload, timeout=30)
                response.raise_for_status()
                result = response.json()
                reply = result.get("reply") or result.get("message") or result.get("answer")
                if reply:
                    return {"status": "ok", "reply": str(reply)}
                return {"status": "ok", "reply": "Mark received a response, but it did not include a reply field."}
            except Exception as exc:
                self._dashboard_event(f"Chrome companion webhook failed: {exc}")
                return {"status": "error", "message": "Mark could not reach the configured chat service."}

        context_note = "selected text" if selected_text else "this page" if page_text else "no page content"
        knowledge_note = f" I also found {len(knowledge)} relevant saved source(s)." if knowledge else ""
        return {
            "status": "ok",
            "reply": (
                f"I received your message and {context_note}.{knowledge_note} "
                "To get AI-generated answers, add MARK_CHAT_WEBHOOK_URL to .env and connect it to your n8n/AI workflow."
            ),
        }

    def learn_link(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch, extract, and locally index one user-submitted public web page."""
        supplied_token = str(payload.get("token", ""))
        if self.mark_api_token and supplied_token != self.mark_api_token:
            return {"status": "error", "message": "Mark connection token is invalid."}
        url = str(payload.get("url", "")).strip()
        try:
            final_url, html = self._download_public_page(url)
            soup = BeautifulSoup(html, "html.parser")
            for element in soup(["script", "style", "noscript", "svg", "iframe"]):
                element.decompose()
            title = (soup.title.string if soup.title and soup.title.string else final_url).strip()
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()[:60000]
            if len(text) < 80:
                raise ValueError("The page did not contain enough readable text to learn from.")
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except requests.RequestException:
            return {"status": "error", "message": "Mark could not download that link."}

        digest = hashlib.sha256(final_url.encode("utf-8")).hexdigest()[:16]
        source_path = self.knowledge_dir / f"{digest}.json"
        record = {
            "collection": self.capture_collection,
            "url": final_url,
            "title": title[:500],
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "text": text,
        }
        source_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        self._upload_file_to_drive(source_path, "application/json")
        self._update_dashboard(last_action="Learned a web link")
        self._dashboard_event(f"Learned link: {title[:100]}")
        return {
            "status": "ok",
            "message": f"Mark learned '{title[:100]}' ({len(text):,} characters).",
            "source": {"title": title[:500], "url": final_url},
        }

    @staticmethod
    def _validate_public_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Use a normal public http or https link.")
        hostname = parsed.hostname.lower()
        if hostname == "localhost" or hostname.endswith(".local"):
            raise ValueError("Local network links cannot be learned.")
        try:
            addresses = {entry[4][0] for entry in socket.getaddrinfo(hostname, None)}
        except socket.gaierror:
            raise ValueError("Mark could not resolve that link.")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise ValueError("Private or reserved network links cannot be learned.")
        return url

    def _download_public_page(self, url: str) -> Tuple[str, str]:
        current_url = self._validate_public_url(url)
        headers = {"User-Agent": "MarkKnowledgeBot/1.0 (+local personal assistant)"}
        for _ in range(4):
            response = requests.get(current_url, headers=headers, timeout=20, allow_redirects=False, stream=True)
            if response.is_redirect:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("The link redirected without a destination.")
                from urllib.parse import urljoin
                current_url = self._validate_public_url(urljoin(current_url, location))
                continue
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type:
                raise ValueError("Mark can learn web pages only; this link is not HTML.")
            chunks, total = [], 0
            for chunk in response.iter_content(chunk_size=16384):
                total += len(chunk)
                if total > 3_000_000:
                    raise ValueError("This page is too large to learn in one request.")
                chunks.append(chunk)
            return current_url, b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
        raise ValueError("This link redirected too many times.")

    def _find_relevant_knowledge(self, query: str) -> List[Dict[str, str]]:
        """Return small excerpts from the most relevant locally saved link notes."""
        terms = {term for term in re.findall(r"[a-zA-Z0-9]{3,}", query.lower())}
        if not terms:
            return []
        matches = []
        for source_path in sorted(self.knowledge_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:200]:
            try:
                record = json.loads(source_path.read_text(encoding="utf-8"))
                searchable = f"{record.get('title', '')} {record.get('text', '')}".lower()
                score = sum(searchable.count(term) for term in terms)
                if score:
                    matches.append((score, record))
            except (OSError, json.JSONDecodeError):
                continue
        matches.sort(key=lambda item: item[0], reverse=True)
        return [
            {"title": str(record.get("title", ""))[:500], "url": str(record.get("url", ""))[:2048], "excerpt": str(record.get("text", ""))[:3500]}
            for _, record in matches[:3]
        ]

    def handle_hud_feature(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Run a HUD button without blocking the overlay on TTS or long workflows."""
        feature = str(payload.get("feature") or payload.get("command") or "").strip().lower()
        query = str(payload.get("query") or payload.get("message") or payload.get("url") or "").strip()
        if not feature:
            return {"status": "error", "message": "feature is required"}

        ack = {
            "mic": "Microphone standing by.",
            "listen": "Voice listening resumed.",
            "pause": "Voice listening paused.",
            "screen": "Reading the screen now.",
            "leads": "Lead scan initiated.",
            "arbitrage": "Arbitrage workflow started.",
            "research": "Research agent engaged.",
            "email": "Email agent engaged.",
            "proposal": "Drafting proposal.",
            "web": "Opening a web search.",
            "learn": "Indexing that source.",
            "test": "Running diagnostics.",
            "help": "Command list ready.",
            "dashboard": "Opening the full command center.",
            "lang": "Cycling language.",
            "language:en": "English selected.",
            "language:hi": "Hindi selected.",
            "language:gu": "Gujarati selected.",
            "shutdown": "Powering down.",
        }.get(feature, "On it, Sir.")

        self._update_dashboard(assistant_voice=ack, last_action=f"HUD: {feature}")
        thread = threading.Thread(
            target=self._dispatch_hud_feature,
            args=(feature, query),
            daemon=True,
            name=f"hud-{feature}",
        )
        thread.start()
        return {"status": "ok", "message": ack, "feature": feature, "reply": ack}

    def handle_hud_window(self, action: str) -> Dict[str, Any]:
        """Close or minimize the overlay GUI window. Mark can keep running."""
        action = (action or "").strip().lower()
        if action in {"close", "hide", "dismiss"}:
            self.close_hud_overlay()
            self._update_dashboard(assistant_voice="Overlay window closed.")
            return {"status": "ok", "message": "Overlay window closed."}
        if action in {"minimize", "min"}:
            minimized = self._minimize_hud_windows()
            return {
                "status": "ok",
                "message": "Overlay minimized." if minimized else "Could not find overlay window to minimize.",
            }
        return {"status": "error", "message": "action must be close or minimize"}

    def close_hud_overlay(self) -> None:
        def _kill():
            time.sleep(0.2)
            proc = getattr(self, "_hud_process", None)
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            self._hud_process = None
            self._minimize_hud_windows(close=True)
            lock = Path(__file__).with_name(".hud.lock")
            try:
                if lock.exists():
                    lock.unlink()
            except Exception:
                pass
            self._dashboard_event("Overlay HUD closed")

        threading.Thread(target=_kill, daemon=True, name="hud-close").start()

    def _minimize_hud_windows(self, close: bool = False) -> bool:
        if os.name != "nt":
            return False
        user32 = ctypes.windll.user32
        found: list[int] = []
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value or ""
                if "J.A.R.V.I.S" in title or title.strip() == "J.A.R.V.I.S.":
                    found.append(int(hwnd))
            return True

        try:
            user32.EnumWindows(EnumWindowsProc(_cb), 0)
        except Exception:
            return False
        SW_MINIMIZE = 6
        WM_CLOSE = 0x0010
        acted = False
        for hwnd in found:
            try:
                if close:
                    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                else:
                    user32.ShowWindow(hwnd, SW_MINIMIZE)
                acted = True
            except Exception:
                continue
        return acted

    def _dispatch_hud_feature(self, feature: str, query: str) -> None:
        """Execute a HUD feature on a worker thread."""
        try:
            if feature in {"mic", "listen", "start_listening"}:
                self.handle_dashboard_command("start_listening")
                return
            if feature in {"pause", "stop_listening"}:
                self.handle_dashboard_command("stop_listening")
                return
            if feature in {"screen", "read_screen"}:
                self.handle_read_screen(query)
                return
            if feature in {"leads", "lead_generation"}:
                self.handle_lead_generation(query or "find freelance clients")
                return
            if feature in {"arbitrage", "arbitrage_lead_generation"}:
                self.handle_arbitrage_lead_generation(query or "Urgent React Developer job posting")
                return
            if feature in {"research", "research_agent"}:
                self.handle_research(query or "latest market research")
                return
            if feature in {"email", "email_agent"}:
                self.handle_email(query or "draft a follow-up email")
                return
            if feature in {"proposal", "create_proposal"}:
                self.execute_action("create_proposal", {"query": query or "client proposal"})
                return
            if feature in {"web", "web_search"}:
                import webbrowser
                from urllib.parse import quote_plus
                webbrowser.open(f"https://duckduckgo.com/?q={quote_plus(query or 'news')}")
                self.speak("Browser search opened.")
                self._update_dashboard(last_action="Web search opened")
                return
            if feature == "learn":
                result = self.learn_link({"url": query})
                message = result.get("message") or result.get("reply") or "Learn finished."
                self.speak(message)
                self._update_dashboard(assistant_voice=message)
                return
            if feature in {"test", "self_test"}:
                result = self.run_self_test({})
                message = result.get("message") or "Diagnostics complete."
                self.speak(message)
                self._update_dashboard(assistant_voice=message)
                return
            if feature == "help":
                text = (
                    "I can find leads, research, draft email, write proposals, "
                    "read the screen, learn a link, search the web, switch language, "
                    "and talk with you from this overlay."
                )
                self.speak(text)
                self._update_dashboard(assistant_voice=text)
                return
            if feature in {"dashboard", "open_dashboard"}:
                import webbrowser
                webbrowser.open("http://127.0.0.1:8080/")
                self.speak("Full dashboard opened.")
                return
            if feature == "lang":
                current = (self.speech_language or "en-US").lower()
                nxt = "hi" if current.startswith("en") else "gu" if current.startswith("hi") else "en"
                self.handle_dashboard_command(f"language:{nxt}")
                return
            if feature.startswith("language:") or feature.startswith("lang:"):
                self.handle_dashboard_command(feature.replace("lang:", "language:"))
                return
            if feature == "shutdown":
                self.handle_dashboard_command("shutdown")
                self.handle_hud_window("close")
                return
            if feature.startswith("action:"):
                self.handle_dashboard_command(feature)
                return
            self.handle_dashboard_command(feature)
        except Exception as exc:
            self._dashboard_event(f"HUD feature '{feature}' failed: {exc}")
            self._update_dashboard(assistant_voice=f"That feature failed: {exc}")

    def handle_dashboard_command(self, command_name: str) -> dict:
        """Handle dashboard-triggered commands from the browser."""
        if command_name == "read_screen":
            self.handle_read_screen("")
            return {"status": "ok", "message": "Screen reader activated."}
        if command_name == "stop_listening":
            self.is_listening = False
            self._update_dashboard(status="Paused", last_action="Listening paused")
            self.speak("Voice listening paused. Use the dashboard or say start listening to resume.")
            return {"status": "ok", "message": "Listening paused."}
        if command_name == "start_listening":
            self.is_listening = True
            self._update_dashboard(status="Listening", last_action="Listening resumed")
            self.speak("Voice listening resumed. I am ready.")
            return {"status": "ok", "message": "Listening resumed."}
        if command_name == "shutdown":
            self._stop_requested = True
            self._update_dashboard(status="Shutting down", last_action="Shutdown requested")
            self.speak("Shutting down now.")
            self.close_hud_overlay()
            return {"status": "ok", "message": "Shutdown requested."}
        if command_name.startswith("action:"):
            # remote trigger of an action, format: action:action_name
            try:
                _, act = command_name.split(":", 1)
            except Exception:
                return {"status": "error", "message": "invalid action command"}
            try:
                res = self.execute_action(act.strip(), {})
                return {"status": "ok", "message": str(res)}
            except Exception as exc:
                return {"status": "error", "message": str(exc)}
        # training via dashboard: format "train:intent|phrase1;phrase2"
        if command_name.startswith("train:"):
            try:
                _, payload = command_name.split(":", 1)
                intent, phrases = payload.split("|", 1)
                phrase_list = [p.strip() for p in phrases.split(";") if p.strip()]
                if not intent or not phrase_list:
                    raise ValueError("invalid train payload")
                self.train_command(intent.strip(), phrase_list)
                return {"status": "ok", "message": f"Trained {intent} with {len(phrase_list)} phrases"}
            except Exception as exc:
                return {"status": "error", "message": f"train failed: {exc}"}
        # language switch: 'language:hi' or 'language:gu' or 'language:en'
        if command_name.startswith("language:") or command_name.startswith("lang:"):
            try:
                _, code = command_name.split(":", 1)
                code = code.strip().lower()
                mapping = {
                    "hi": "hi-IN",
                    "hin": "hi-IN",
                    "hi-in": "hi-IN",
                    "gu": "gu-IN",
                    "guj": "gu-IN",
                    "gu-in": "gu-IN",
                    "en": "en-US",
                    "en-us": "en-US",
                    "en-in": "en-IN",
                }
                lang = mapping.get(code, code)
                self.speech_language = lang
                self._update_dashboard(status=f"Listening ({lang})", last_action="Language changed")
                self.speak(f"Language set to {lang}. I will listen in that language.")
                return {"status": "ok", "message": f"language set to {lang}"}
            except Exception as exc:
                return {"status": "error", "message": f"language change failed: {exc}"}
        if command_name == "learner:status":
            try:
                stats = self.learner.stats() if self.learner else {}
                self._refresh_learner_dashboard_state()
                return {"status": "ok", "stats": stats, "top_patterns": (self.learner.top_patterns(n=10) if self.learner else {})}
            except Exception as exc:
                return {"status": "error", "message": str(exc)}
        if command_name == "learner:forget_all":
            try:
                if self.learner:
                    self.learner.forget_all()
                    self._refresh_learner_dashboard_state()
                    self.speak("I have forgotten all learned command patterns.")
                return {"status": "ok", "message": "Learned commands cleared."}
            except Exception as exc:
                return {"status": "error", "message": str(exc)}
        if command_name.startswith("learner:forget_intent:"):
            try:
                _, intent = command_name.split(":", 1)[1].split(":", 1)
            except Exception:
                return {"status": "error", "message": "expected 'learner:forget_intent:INTENT_NAME'"}
            try:
                if self.learner:
                    self.learner.forget_intent(intent.strip())
                    self._refresh_learner_dashboard_state()
                return {"status": "ok", "message": f"Forgot learned data for {intent}"}
            except Exception as exc:
                return {"status": "error", "message": str(exc)}
        return {"status": "error", "message": f"Unknown command: {command_name}"}

    def speak(self, text: str) -> None:
        """Speak text using local TTS and mirror it onto the overlay HUD."""
        if text:
            self._update_dashboard(assistant_voice=text)
        if self.autonomy_level == "auto" and len(text) < 30:
            self._speak_fallback(text)
        else:
            try:
                self._speak_fallback(text)
            except Exception:
                pass

    def _speak_fallback(self, text: str) -> None:
        """Fallback text-to-speech via pyttsx3. Must be serialized; SAPI is not thread-safe."""
        if not self.engine:
            return
        lock = getattr(self, "_tts_lock", None)
        if lock is None:
            self._tts_lock = threading.Lock()
            lock = self._tts_lock
        with lock:
            try:
                self.engine.say(text)
                self.engine.runAndWait()
            except Exception:
                pass

    def _set_persona_ironman(self) -> None:
        """Adjust voice settings and HUD persona to feel like Iron Man."""
        if not self.engine:
            return
        try:
            # set a brisk speech rate and prefer a male voice if available
            self.engine.setProperty("rate", 160)
            voices = self.engine.getProperty("voices")
            if voices:
                for v in voices:
                    name = getattr(v, 'name', '').lower()
                    if 'male' in name or 'david' in name or 'michael' in name:
                        self.engine.setProperty('voice', v.id)
                        break
        except Exception:
            pass

    def _init_automation_tools(self) -> None:
        """Try to import automation tools and set flags for runtime use."""
        self._has_pyautogui = False
        self._has_playwright = False
        self._has_screen_ocr = False
        try:
            import pyautogui
            self.pyautogui = pyautogui
            self._has_pyautogui = True
        except Exception:
            self._dashboard_event("pyautogui not available")
        try:
            from playwright.sync_api import sync_playwright
            self.sync_playwright = sync_playwright
            self._has_playwright = True
        except Exception:
            self._dashboard_event("playwright not available")
        try:
            import pytesseract
            from PIL import ImageGrab
            self.pytesseract = pytesseract
            self.ImageGrab = ImageGrab
            self._has_screen_ocr = True
            self._configure_tesseract_cmd()
        except Exception:
            self._dashboard_event("screen OCR unavailable (pytesseract/Pillow not installed)")

    def execute_action(self, action: str, params: Dict) -> Dict:
        """Execute a high-level action after checking allowlist and autonomy policy.

        Returns a dict with `status` and `detail`.
        """
        action = action.strip()
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        allowed = (not self.allowlist) or (action in self.allowlist)
        if not allowed:
            msg = f"Action '{action}' not in allowlist. Skipping."
            self._dashboard_event(msg)
            self._log_action(timestamp, action, params, "blocked")
            self.speak("Action blocked by allowlist.")
            return {"status": "blocked", "detail": msg}

        # Auto mode executes without confirmation
        if self.autonomy_level == "manual":
            self.speak(f"Ready to run {action}. Say yes to proceed.")
            if not self._confirm_choice():
                self._log_action(timestamp, action, params, "cancelled")
                return {"status": "cancelled"}

        # Dispatch known actions
        try:
            if action == "create_proposal":
                query = params.get("query", "")
                leads = params.get("leads", [])
                freelancers = params.get("freelancers", [])
                proposal = self._draft_arbitrage_proposal(query, leads, freelancers)
                self._save_proposal_locally(proposal)
                res = {"status": "ok", "detail": "proposal_created"}
            elif action == "send_email":
                payload = params.get("payload", {"query": params.get("query", "")})
                self.trigger_n8n_workflow("EmailAgent", payload)
                res = {"status": "ok", "detail": "email_triggered"}
            elif action == "browser_fill":
                if not self._has_playwright:
                    raise RuntimeError("playwright not available")
                if not self._request_permission("browser_automation", blocking=True):
                    raise PermissionError("Browser automation permission not granted")
                url = params.get("url")
                fields = params.get("fields", {})
                with self.sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    page = browser.new_page()
                    page.goto(url)
                    for sel, val in fields.items():
                        try:
                            page.fill(sel, val)
                        except Exception:
                            pass
                    browser.close()
                res = {"status": "ok", "detail": "browser_filled"}
            elif action == "file_move":
                src = params.get("src")
                dst = params.get("dst")
                if not src or not dst:
                    raise RuntimeError("src and dst required")
                os.replace(src, dst)
                res = {"status": "ok", "detail": "file_moved"}
            elif action == "search_leads":
                q = params.get("query", "")
                leads = self.search_leads(q, limit=params.get("limit", 10))
                res = {"status": "ok", "leads": leads}
            else:
                # fallback: send to n8n as a named agent
                self.trigger_n8n_workflow(action, params)
                res = {"status": "ok", "detail": "delegated_to_n8n"}

            self._log_action(timestamp, action, params, res.get("status"))
            # brief confirmation for the HUD
            self._update_dashboard(last_action=f"Executed {action}")
            if self.autonomy_level == "auto":
                self.speak("Done, Sir")
            else:
                self.speak(f"Action {action} completed.")
            return res
        except Exception as exc:
            self._log_action(timestamp, action, params, f"error: {exc}")
            self._dashboard_event(f"Action {action} failed: {exc}")
            self.speak("Action failed.")
            return {"status": "error", "detail": str(exc)}

    def _log_action(self, timestamp: str, action: str, params: Dict, status: str) -> None:
        try:
            header = not self.action_log_path.exists()
            with self.action_log_path.open("a", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                if header:
                    writer.writerow(["timestamp", "action", "params", "status"])
                writer.writerow([timestamp, action, json.dumps(params, ensure_ascii=False), status])
        except Exception as exc:
            print("Failed logging action:", exc)

    def listen(self) -> str:
        """Listen for a voice command and convert speech to text."""
        if not self._request_permission("microphone", blocking=True):
            return ""
        recognizer = sr.Recognizer()
        try:
            with sr.Microphone() as source:
                self.speak("I am listening.")
                recognizer.adjust_for_ambient_noise(source, duration=1)
                audio = recognizer.listen(source, phrase_time_limit=10)
        except Exception as exc:
            self.is_listening = False
            self._update_dashboard(status="Chrome ready — microphone unavailable", last_action="Voice listening paused")
            self._dashboard_event(f"Microphone unavailable: {exc}")
            print(f"Microphone unavailable; Mark will remain available in Chrome: {exc}")
            return ""
        try:
            query = recognizer.recognize_google(audio, language=self.speech_language)
            print(f"User said: {query}")
            self._update_dashboard(current_query=query)
            self._dashboard_event(f"Heard query: {query}")
            return query
        except sr.UnknownValueError:
            self.speak("I did not understand that. Please repeat.")
            self._dashboard_event("Voice input not recognized")
            return ""
        except sr.RequestError:
            self.speak("Voice recognition service is unavailable.")
            self._dashboard_event("Speech recognition service unavailable")
            return ""

    def detect_intent(self, query: str) -> Tuple[str, float]:
        """Determine intent with fuzzy matching, keyword fallback, and learner boost.

        Returns (intent, confidence) where confidence is 0.0-1.0.
        The lightweight command learner adds a second opinion: if its confidence is
        strong and the base match is weak, it can override the fuzzy matcher so the
        system visibly "learns" your phrasing over time.
        """
        normalized = query.lower().strip()
        best_intent = "general_agent"
        best_score = 0.0

        if self.commands:
            for intent, phrases in self.commands.items():
                for phrase in phrases:
                    if not phrase:
                        continue
                    score = SequenceMatcher(None, phrase, normalized).ratio()
                    if score > best_score:
                        best_score = score
                        best_intent = intent

        if best_score < self.intent_confidence_threshold:
            for intent, keywords in {
                "arbitrage_lead_generation": ["arbitrage", "urgent react", "finders fee"],
                "lead_generation": ["lead", "freelancer", "job posting"],
                "email_agent": ["email", "send mail"],
                "research_agent": ["research", "search", "scrape"],
            }.items():
                if any(kw in normalized for kw in keywords):
                    best_intent = intent
                    best_score = 0.8
                    break

        # Blend with learned command patterns (cheap O(1) snapshot read).
        blended_intent, blended_score, _learn_note = self._learner_boost_intent(query, best_intent, best_score)
        if blended_intent and blended_score:
            best_intent = blended_intent
            best_score = blended_score

        self.last_query = normalized
        self.last_intent = best_intent
        self.conversation_history.append({"query": normalized, "intent": best_intent, "confidence": best_score})
        if len(self.conversation_history) > 50:
            self.conversation_history.pop(0)

        return best_intent, best_score

    def _load_command_phrases(self) -> dict:
        """Load or create the commands.json mapping.

        Returns a dict mapping intent -> list[str].
        """
        try:
            if self.commands_file.exists():
                import json as _json
                return _json.loads(self.commands_file.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def train_command(self, intent: str, phrases: list[str]) -> None:
        """Add phrases to an intent and persist to `commands.json`."""
        try:
            existing = self.commands or {}
            bucket = set(existing.get(intent, []))
            for p in phrases:
                bucket.add(p.lower().strip())
            existing[intent] = sorted(bucket)
            import json as _json
            self.commands_file.write_text(_json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
            self.commands = existing
            self._dashboard_event(f"Trained intent {intent} (+{len(phrases)})")
        except Exception as exc:
            self._dashboard_event(f"Training failed: {exc}")

    def execute_intent(self, query: str) -> None:
        """Route the user query to the appropriate workflow, record it for learning."""
        intent, confidence = self.detect_intent(query)

        # Learner-specific voice commands (bypass confidence gate — they are commands
        # about the learner itself, not workflow routing).
        acted = False
        learner_handled = self._try_handle_learner_voice_command(query)
        if learner_handled:
            self._record_command_to_learner(query, "learner_admin", 0.95, acted=True)
            return

        if confidence < self.intent_confidence_threshold:
            self.speak(f"I'm not confident about that command. Please repeat or try a different phrase.")
            self._dashboard_event(f"Low confidence intent {intent} ({confidence:.2%}): '{query}'")
            return

        if intent in ["stop_listening_agent", "start_listening_agent", "shutdown_agent"]:
            self.handle_control_command(intent)
            self._record_command_to_learner(query, intent, confidence, acted=True)
            return

        mode = self._classify_query_mode(query)
        self.speak(f"Intent detected: {intent.replace('_', ' ')}.")
        self._update_dashboard(intent=intent, last_action="Intent detected")
        self._dashboard_event(f"Intent routed: {intent} (confidence: {confidence:.2%}, mode: {mode})")

        if mode == "ambiguous":
            candidates = [
                {"mode": "action", "explanation": f"Perform the {intent.replace('_', ' ')} workflow"},
                {"mode": "info", "explanation": f"Provide information about {query}"},
            ]
            choice = self._propose_alternatives(query, candidates)
            if choice == "action":
                mode = "action"
            elif choice == "info":
                mode = "info"

        if mode == "action":
            plan = self._generate_react_plan(intent, query)
            if plan:
                brief = plan[0]
                self.speak(f"Planned: {brief}. Confirm to proceed?")
                confirmed = self._confirm_choice()
                if not confirmed:
                    self.speak("Cancelled.")
                    self._record_command_to_learner(query, intent, confidence, acted=False)
                    return
            if intent == "arbitrage_lead_generation":
                self.handle_arbitrage_lead_generation(query)
                acted = True
            elif intent == "read_screen_agent":
                self.handle_read_screen(query)
                acted = True
            elif intent == "lead_generation":
                self.handle_lead_generation(query)
                acted = True
            elif intent == "research_agent":
                self.handle_research(query)
                acted = True
            elif intent == "email_agent":
                self.handle_email(query)
                acted = True
            else:
                self.trigger_n8n_workflow("GeneralAgent", {"query": query, "intent": intent})
                acted = True
        else:
            self.trigger_n8n_workflow("ResearchAgent", {"query": query, "intent": intent})
            acted = True

        self._record_command_to_learner(query, intent, confidence, acted=acted)
        self._refresh_learner_dashboard_state()

    def _try_handle_learner_voice_command(self, query: str) -> bool:
        """Respond to simple learner admin voice commands. Returns True if handled."""
        q = (query or "").lower().strip()
        if not q:
            return False

        has_keyword = any(phrase in q for phrase in ["learner", "learn", "command memory", "training data", "what have you learned"])
        if not has_keyword:
            return False

        if any(p in q for p in ["status", "stats", "how many", "summary", "how much"]):
            stats = self.learner.stats() if self.learner else {}
            held = stats.get("samples_held", 0)
            distinct = stats.get("distinct_intents", 0)
            disk_kb = round((stats.get("storage_bytes", 0) or 0) / 1024, 1)
            mem_kb = round((stats.get("approx_mem_bytes", 0) or 0) / 1024, 1)
            top = stats.get("top_intents") or []
            parts = [
                f"I've captured {held} command examples across {distinct} intent types so far.",
                f"Storage uses {disk_kb} kilobytes on disk, and about {mem_kb} kilobytes in memory.",
            ]
            if top:
                names = ", ".join(
                    f"{t['intent'].replace('_', ' ')} ({t['count']})" for t in top[:3]
                )
                parts.append(f"Most common actions: {names}.")
            msg = " ".join(parts)
            self.speak(msg)
            self._dashboard_event(f"Learner status: {held} rows, {distinct} intents, {disk_kb} KB on disk")
            return True

        if any(p in q for p in ["forget everything", "wipe memory", "clear all learned", "forget all commands"]):
            self.speak("I will forget every command I've learned. Confirm by saying yes.")
            if self._confirm_choice():
                self.learner.forget_all()
                self._refresh_learner_dashboard_state()
                self.speak("Done. All learned command patterns have been cleared.")
            else:
                self.speak("Learned memory was left intact.")
            return True

        if any(p in q for p in ["forget intent", "remove intent", "clear intent"]):
            remaining = q
            for p in ["forget intent", "remove intent", "clear intent", "forget", "intent", "for"]:
                remaining = remaining.replace(p, "")
            remaining = remaining.strip(" ,.?!-_")
            tokens = remaining.split()
            if not tokens:
                self.speak("Tell me the name of the intent to forget.")
            else:
                candidate = "_".join(tokens)
                self.speak(f"I will forget all learned '{candidate.replace('_', ' ')}' commands. Confirm?")
                if self._confirm_choice():
                    self.learner.forget_intent(candidate)
                    self._refresh_learner_dashboard_state()
                    self.speak(f"Done. Cleared learned patterns for {candidate.replace('_', ' ')}.")
                else:
                    self.speak("Nothing was forgotten.")
            return True

        if any(p in q for p in ["patterns", "top patterns", "common phrases", "what phrases"]):
            patterns = self.learner.top_patterns(n=5) if self.learner else {}
            if not patterns:
                self.speak("I haven't learned any strong phrase patterns yet. Give me a few commands.")
                return True
            out = ["Top learned patterns:"]
            for intent, rows in list(patterns.items())[:3]:
                joined = ", ".join(f"'{r['pattern']}' x{r['count']}" for r in rows[:3])
                out.append(f"{intent.replace('_', ' ')}: {joined}.")
            msg = " ".join(out)
            self.speak(msg)
            self._dashboard_event("Reported top learned command patterns by voice")
            return True

        return False

    def handle_lead_generation(self, query: str) -> None:
        """Lead generation workflow: search leads, save them, and notify n8n."""
        self.speak("Starting lead generation workflow.")
        self._update_dashboard(last_action="Lead generation started")
        self._dashboard_event("Lead generation workflow triggered")
        leads = self.search_leads(query, limit=10)
        self.dashboard.update_leads(leads)
        if leads:
            self.save_leads_to_google_sheet(leads)
            self.speak(f"Saved {len(leads)} leads to Google Sheets.")
            self._update_dashboard(last_saved=time.strftime("%Y-%m-%d %H:%M:%S"))
        self.trigger_n8n_workflow("LeadGenerationAgent", {"query": query, "leads": leads})

    def handle_arbitrage_lead_generation(self, query: str) -> None:
        """Arbitrage workflow: find urgent roles and match them to your freelancer network."""
        self.speak("Starting arbitrage lead generation workflow.")
        self._update_dashboard(last_action="Arbitrage lead gen started")
        self._dashboard_event("Arbitrage lead generation workflow triggered")

        search_query = query if query else "Urgent React Developer job posting"
        leads = self.search_leads(search_query, limit=8)
        self.dashboard.update_leads(leads)

        freelancers = self._load_freelancer_network()
        if freelancers:
            self._dashboard_event(f"Loaded {len(freelancers)} freelancers from network")
        else:
            self._dashboard_event("No freelancer network found; create freelancer_network.csv")

        proposal = self._draft_arbitrage_proposal(search_query, leads, freelancers)
        self._save_proposal_locally(proposal)
        self.speak("Draft proposal is ready for your review.")
        self._update_dashboard(last_saved=time.strftime("%Y-%m-%d %H:%M:%S"), last_action="Arbitrage proposal drafted")
        self.trigger_n8n_workflow("ArbitrageLeadAgent", {"query": search_query, "proposal": proposal, "freelancers": freelancers, "leads": leads})

    def search_leads(self, query: str, limit: int = 10) -> List[Dict[str, str]]:
        """Perform a free DuckDuckGo search with quality filtering for better results."""
        try:
            url = "https://html.duckduckgo.com/html/"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            response = requests.post(url, headers=headers, data={"q": query}, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            results = []
            for item in soup.select("a.result__a")[:limit * 2]:  # fetch extra to filter
                title = item.get_text(strip=True)
                link = item.get("href", "")
                snippet = item.find_parent().select_one("a.result__snippet")
                notes = snippet.get_text(strip=True) if snippet else ""
                
                # quality filter: skip low-quality or spam results
                if not link or len(title) < 3 or "google" in link.lower():
                    continue
                if not notes or len(notes) < 10:
                    continue
                
                results.append({"name": title, "website": link, "notes": notes, "quality_score": len(notes)})
            
            # sort by quality (snippet length) and trim to limit
            results.sort(key=lambda x: x.get("quality_score", 0), reverse=True)
            return results[:limit]
        except Exception as exc:
            print("Free web search failed:", exc)
            self.speak("Lead search failed; continuing with workflow.")
            return []

    def _classify_query_mode(self, query: str) -> str:
        """Classify query as 'action', 'info', or 'ambiguous'."""
        q = (query or "").strip().lower()
        if not q:
            return "info"
        action_verbs = ["create", "send", "draft", "start", "save", "upload", "find", "generate", "run", "trigger"]
        info_markers = ["what", "who", "when", "how", "balance", "status", "where", "why"]
        has_action = any(q.startswith(v) or f" {v} " in q for v in action_verbs)
        has_info = any(q.startswith(v) or f" {v} " in q for v in info_markers)
        if has_action and not has_info:
            return "action"
        if has_info and not has_action:
            return "info"
        return "ambiguous"

    def _generate_react_plan(self, intent: str, query: str) -> List[str]:
        """Return a short 1-3 step plan for an action intent.

        This is a lightweight reason-then-act planner used for confirmations.
        """
        plans = []
        if intent == "arbitrage_lead_generation":
            plans = [
                "Search for urgent job postings matching the query",
                "Match postings to freelancer network",
                "Draft and save a proposal locally"
            ]
        elif intent == "lead_generation":
            plans = ["Search for potential leads and save them to leads.csv and Drive"]
        elif intent == "email_agent":
            plans = ["Draft and send an email via the EmailAgent workflow"]
        elif intent == "research_agent":
            plans = ["Run research routine and summarize findings"]
        elif intent == "read_screen_agent":
            plans = ["Read active window title, clipboard, and dashboard summary aloud"]
        else:
            plans = [f"Run the {intent} workflow"]
        return plans

    def _propose_alternatives(self, query: str, candidates: List[Dict[str, str]]) -> Optional[str]:
        """Speak two top interpretations and listen for a quick A/B choice."""
        try:
            self.speak("I found two possible actions. Option A: " + candidates[0]["explanation"] + ". Option B: " + candidates[1]["explanation"] + ". Say A or B.")
            choice = self.listen().strip().lower()
            if choice.startswith("a"):
                return "action"
            if choice.startswith("b"):
                return "info"
        except Exception:
            pass
        return None

    def _confirm_choice(self) -> bool:
        """Listen for a yes/no confirmation. Returns True if confirmed."""
        answer = self.listen().strip().lower()
        if not answer:
            return False
        if any(word in answer for word in ["yes", "sure", "do it", "y", "confirm"]):
            return True
        return False

    def handle_research(self, query: str) -> None:
        """Research workflow: send the query to the ResearchAgent in n8n."""
        self.speak("Running the research agent.")
        self.trigger_n8n_workflow("ResearchAgent", {"query": query})

    def handle_control_command(self, intent: str) -> None:
        """Handle internal assistant control commands like pause, resume, and shutdown."""
        if intent == "stop_listening_agent":
            self.is_listening = False
            self._update_dashboard(status="Paused", last_action="Voice listening paused")
            self.speak("Voice listening paused. Use the dashboard button to resume.")
            return
        if intent == "start_listening_agent":
            self.is_listening = True
            self._update_dashboard(status="Listening", last_action="Voice listening resumed")
            self.speak("Voice listening resumed. I am ready.")
            return
        if intent == "shutdown_agent":
            self._stop_requested = True
            self._update_dashboard(status="Shutting down", last_action="Shutdown requested")
            self.speak("Shutting down now.")
            return

    def handle_email(self, query: str) -> None:
        """Email workflow: send the query to the EmailAgent in n8n."""
        self.speak("Triggering the email agent.")
        self.trigger_n8n_workflow("EmailAgent", {"query": query})

    def trigger_n8n_workflow(self, agent_name: str, payload: Dict) -> Optional[Dict]:
        """Send a payload to the configured n8n webhook."""
        if not self.n8n_webhook_url:
            self.speak("n8n webhook URL is not configured.")
            return None
        try:
            body = {"agent": agent_name, "payload": payload}
            response = requests.post(self.n8n_webhook_url, json=body, timeout=20)
            response.raise_for_status()
            self._update_dashboard(n8n_status=f"{agent_name} dispatched")
            self._dashboard_event(f"n8n webhook sent for {agent_name}")
            return response.json()
        except Exception as exc:
            print("n8n webhook failed:", exc)
            self.speak("Failed to reach the automation workflow.")
            self._update_dashboard(n8n_status="n8n failed")
            self._dashboard_event("n8n webhook failed")
            return None

    def handle_read_screen(self, query: str) -> None:
        """Speak the current screen summary, clipboard content, and dashboard context."""
        self.speak("Reading the screen and current assistant status.")
        self._update_dashboard(last_action="Screen reader activated")
        active_title = self._get_active_window_title()
        clipboard_text = self._get_clipboard_text()
        screen_text = self._get_screen_text()
        summary = self._summarize_dashboard_for_screen_reader()

        if active_title:
            self.speak(f"Active window: {active_title}.")
        if clipboard_text:
            self.speak("Here is the text from your clipboard.")
            self.speak(clipboard_text)
        if screen_text:
            self.speak("Reading the visible screen text now.")
            self._speak_screen_text_lines(screen_text)
        else:
            if self._has_screen_ocr:
                self.speak("I could not detect readable text on the visible screen.")
            else:
                self.speak(
                    "Screen OCR is not available. Install Pillow, pytesseract, and the Tesseract OCR engine to enable full screen reading."
                )
        self.speak(summary)
        self._dashboard_event("Screen reading completed")

    def _get_active_window_title(self) -> str:
        """Return the active window title on Windows."""
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
            return buffer.value
        except Exception:
            return ""

    def _get_clipboard_text(self) -> str:
        """Return clipboard text when available. Avoid tkinter; it can crash off the main thread."""
        if not self._request_permission("clipboard_access", blocking=True):
            return ""
        try:
            CF_UNICODETEXT = 13
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            if not user32.OpenClipboard(None):
                return ""
            try:
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return ""
                locked = kernel32.GlobalLock(handle)
                if not locked:
                    return ""
                try:
                    text = ctypes.wstring_at(locked)
                finally:
                    kernel32.GlobalUnlock(handle)
                return (text or "").strip()
            finally:
                user32.CloseClipboard()
        except Exception:
            return ""

    def _get_screen_text(self) -> str:
        """Capture the visible screen and extract readable text via OCR."""
        if not self._has_screen_ocr:
            return ""
        if not self._request_permission("screen_capture", blocking=True):
            return ""
        try:
            screenshot = self.ImageGrab.grab()
            gray = screenshot.convert("L")
            text = self.pytesseract.image_to_string(gray, lang="eng")
            return text.strip()
        except Exception as exc:
            self._dashboard_event(f"Screen OCR failed: {exc}")
            return ""

    def _configure_tesseract_cmd(self) -> None:
        """Configure pytesseract to use the default Windows Tesseract executable path."""
        try:
            if not getattr(self.pytesseract, 'tesseract_cmd', None):
                self.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        except Exception:
            pass

    def _speak_screen_text_lines(self, text: str) -> None:
        """Speak detected screen text line by line."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            self.speak("No readable text was found on the screen.")
            return
        max_lines = 80
        for idx, line in enumerate(lines[:max_lines], start=1):
            self.speak(f"Line {idx}: {line}")
        if len(lines) > max_lines:
            self.speak(f"Screen text truncated. I read the first {max_lines} lines.")

    def _summarize_dashboard_for_screen_reader(self) -> str:
        """Build a short spoken summary of the dashboard state."""
        state = self.dashboard.get_state() if self.dashboard else {}
        last_action = state.get("last_action", "no recent action")
        intent = state.get("intent", "no intent detected")
        lead_count = len(state.get("leads", [])) if isinstance(state.get("leads", []), list) else 0
        return (
            f"Dashboard status: {state.get('status', 'offline')}. "
            f"Last action: {last_action}. "
            f"Current intent: {intent}. "
            f"Lead count: {lead_count}."
        )

    def _build_gspread_client(self):
        """Initialize Google Sheets client when needed."""
        if self.gspread_client is not None:
            return self.gspread_client
        if not self.google_credentials or not self.sheet_id:
            return None
        try:
            import gspread
            from google.oauth2.service_account import Credentials
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            credentials = Credentials.from_service_account_file(self.google_credentials, scopes=scopes)
            self.gspread_client = gspread.authorize(credentials)
            return self.gspread_client
        except Exception as exc:
            print("Google Sheets client setup failed:", exc)
            self.speak("Google Sheets integration is not available.")
            return None

    def _build_drive_service(self):
        """Initialize Google Drive client when needed."""
        if self.drive_service is not None:
            return self.drive_service
        if not self.google_credentials or not self.drive_folder_id:
            return None
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
            scopes = [
                "https://www.googleapis.com/auth/drive.file",
                "https://www.googleapis.com/auth/drive.metadata.readonly",
            ]
            credentials = Credentials.from_service_account_file(self.google_credentials, scopes=scopes)
            self.drive_service = build("drive", "v3", credentials=credentials)
            return self.drive_service
        except Exception as exc:
            print("Google Drive client setup failed:", exc)
            self.speak("Google Drive storage is not available.")
            return None

    def save_leads_to_google_sheet(self, leads: List[Dict[str, str]]) -> None:
        """Append search leads into the configured Google Sheet or fallback to Drive/CSV storage."""
        client = self._build_gspread_client()
        if client:
            try:
                sheet = client.open_by_key(self.sheet_id).sheet1
                rows = [[
                    lead.get("name", ""),
                    lead.get("email", ""),
                    lead.get("company", ""),
                    lead.get("website", ""),
                    lead.get("notes", "") or ""
                ] for lead in leads]
                if rows:
                    sheet.append_rows([
                        ["Name", "Email", "Company", "Website", "Notes"]
                    ] if sheet.row_count == 0 else [] + rows, value_input_option="RAW")
                return
            except Exception as exc:
                print("Failed saving leads to Google Sheets:", exc)
                self.speak("Could not save leads to Google Sheets; saving locally instead.")

        self._save_leads_locally(leads)

    def _save_leads_locally(self, leads: List[Dict[str, str]]) -> None:
        """Save leads to a local CSV file and upload to Google Drive if configured."""
        if not leads:
            return
        try:
            file_exists = self.local_leads_path.exists()
            with self.local_leads_path.open("a", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)
                if not file_exists:
                    writer.writerow(["Name", "Email", "Company", "Website", "Notes"])
                for lead in leads:
                    writer.writerow([
                        lead.get("name", ""),
                        lead.get("email", ""),
                        lead.get("company", ""),
                        lead.get("website", ""),
                        lead.get("notes", "") or "",
                    ])
            self.speak(f"Saved {len(leads)} leads locally to {self.local_leads_path.name}.")
            self._upload_file_to_drive(self.local_leads_path, "text/csv")
        except Exception as exc:
            print("Failed saving leads locally:", exc)
            self.speak("Could not save leads locally.")

    def _load_freelancer_network(self) -> List[Dict[str, str]]:
        """Load a local freelancer network CSV file."""
        network_path = Path(__file__).with_name("freelancer_network.csv")
        freelancers = []
        if not network_path.exists():
            return freelancers
        try:
            with network_path.open("r", encoding="utf-8", newline="") as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    freelancers.append({
                        "name": row.get("name", ""),
                        "skills": row.get("skills", ""),
                        "rate": row.get("rate", ""),
                        "contact": row.get("contact", ""),
                    })
        except Exception as exc:
            print("Failed loading freelancer network:", exc)
        return freelancers

    def _draft_arbitrage_proposal(self, query: str, leads: List[Dict[str, str]], freelancers: List[Dict[str, str]]) -> str:
        """Draft a professional, personalized proposal with compelling deal structure."""
        top_lead = leads[0] if leads else {"name": "Potential client", "website": ""}
        top_freelancer = freelancers[0] if freelancers else {"name": "Available freelancer", "skills": "React, Node.js", "contact": "[contact]"}
        
        # extract skill requirements from query
        skills_to_offer = query if query else "specialized technical talent"
        
        proposal = (
            f"TALENT ARBITRAGE PROPOSAL\n"
            f"{'='*50}\n\n"
            f"Client: {top_lead.get('name')}\n"
            f"Lead Source: {top_lead.get('website')}\n"
            f"Opportunity: {skills_to_offer}\n"
            f"Date Prepared: {time.strftime('%Y-%m-%d')}\n\n"
            f"EXECUTIVE SUMMARY\n"
            f"{'-'*50}\n"
            f"We have identified a pre-vetted {skills_to_offer} specialist matching your urgent needs.\n"
            f"This proposal outlines the talent match, commercial terms, and rapid onboarding process.\n\n"
            f"TALENT MATCH\n"
            f"{'-'*50}\n"
            f"Name: {top_freelancer.get('name')}\n"
            f"Skills: {top_freelancer.get('skills')}\n"
            f"Contact: {top_freelancer.get('contact')}\n"
            f"Availability: Immediate\n"
            f"Experience Level: Senior/Verified\n\n"
            f"PROJECT SCOPE\n"
            f"{'-'*50}\n"
            f"Role: {skills_to_offer} Developer/Consultant\n"
            f"Duration: To be agreed with client\n"
            f"Delivery Model: Full-time or contract\n"
            f"Start Date: Immediate (within 48 hours)\n\n"
            f"COMMERCIAL TERMS\n"
            f"{'-'*50}\n"
            f"Option 1: Finders Fee Structure\n"
            f"  → 15% of first 3 months billed work, OR\n"
            f"  → Fixed fee: ₹50,000 - ₹200,000 (based on deal size)\n\n"
            f"Option 2: Revenue Share\n"
            f"  → 10% of contract value\n"
            f"  → Billed monthly upon successful placement\n\n"
            f"Option 3: Hybrid (Fee + Bonus)\n"
            f"  → ₹25,000 upfront fee\n"
            f"  → 5% of first month's billing as completion bonus\n\n"
            f"VALUE PROPOSITION\n"
            f"{'-'*50}\n"
            f"✓ Pre-vetted talent (reduced hiring risk)\n"
            f"✓ Immediate availability (no 2-week notice)\n"
            f"✓ Proven track record in similar projects\n"
            f"✓ No recruitment overhead or hidden costs\n"
            f"✓ Confidential process (NDA available)\n\n"
            f"WHY THIS WORKS\n"
            f"{'-'*50}\n"
            f"Clients face urgent hiring gaps; you provide pre-qualified talent faster than traditional recruitment.\n"
            f"You build relationships, control margin, and scale the model across multiple clients.\n\n"
            f"NEXT STEPS\n"
            f"{'-'*50}\n"
            f"1. Review proposal and talent profile\n"
            f"2. Schedule 30-min intro call with {top_freelancer.get('name')}\n"
            f"3. Agree on terms and sign engagement letter\n"
            f"4. Collect finder's fee upon successful start\n\n"
            f"Contact: [Your Name] | [Your Phone] | [Your Email]\n"
        )
        return proposal

    def _save_proposal_locally(self, proposal: str) -> None:
        """Save the drafted proposal to a local file and upload it to Drive."""
        proposal_path = Path(__file__).with_name("arbitrage_proposal.txt")
        try:
            proposal_path.write_text(proposal, encoding="utf-8")
            self._upload_file_to_drive(proposal_path, "text/plain")
            self._dashboard_event("Arbitrage proposal saved locally and uploaded")
        except Exception as exc:
            print("Failed saving proposal locally:", exc)
            self._dashboard_event("Failed to save arbitrage proposal")

    def _upload_file_to_drive(self, file_path: Path, mime_type: str) -> None:
        """Upload a file to the configured Google Drive folder."""
        drive = self._build_drive_service()
        if not drive:
            return
        try:
            from googleapiclient.http import MediaFileUpload
            file_metadata = {
                "name": file_path.name,
                "parents": [self.drive_folder_id],
            }
            media = MediaFileUpload(str(file_path), mimetype=mime_type, resumable=True)
            drive.files().create(body=file_metadata, media_body=media, fields="id").execute()
            self.speak(f"Uploaded {file_path.name} to Google Drive.")
            self._update_dashboard(drive_upload=f"{file_path.name} uploaded")
            self._dashboard_event(f"Uploaded {file_path.name} to Drive")
        except Exception as exc:
            print("Google Drive upload failed:", exc)
            self.speak("Could not upload file to Google Drive.")
            self._dashboard_event("Drive upload failed")

    def _start_background_services(self) -> None:
        """Start optional background services: camera monitor and proactive alerting."""
        import threading
        t1 = threading.Thread(target=self._camera_monitor_loop, daemon=True)
        t1.start()
        t2 = threading.Thread(target=self._proactive_alert_loop, daemon=True)
        t2.start()
        t3 = threading.Thread(target=self._periodic_self_test_loop, daemon=True)
        t3.start()
        t4 = threading.Thread(target=self._launch_hud_overlay, daemon=True)
        t4.start()

    def _launch_hud_overlay(self) -> None:
        """Open the always-on-top circular HUD after the dashboard is listening."""
        if str(os.getenv("MARK_DISABLE_HUD") or "").lower() in {"1", "true", "yes"}:
            return
        time.sleep(1.5)
        script = Path(__file__).with_name("hud_overlay.py")
        if not script.exists():
            return
        try:
            flags = 0
            if os.name == "nt":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._hud_process = subprocess.Popen(
                [sys.executable, str(script), str(self.dashboard.port if self.dashboard else 8080)],
                cwd=str(Path(__file__).parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            self._dashboard_event("Overlay HUD launched")
        except Exception as exc:
            self._dashboard_event(f"Overlay HUD failed to launch: {exc}")

    def _periodic_self_test_loop(self) -> None:
        """Refresh the readiness report every five minutes while Mark is running."""
        while not self._stop_requested:
            time.sleep(300)
            if not self._stop_requested:
                self._run_startup_checks("Automatic")

    def _camera_monitor_loop(self) -> None:
        """Attempt to access a camera and report availability to the dashboard.

        This is a lightweight, optional hook. If `opencv-python` is not installed
        the loop exits silently. If the camera permission has not been granted, the
        loop simply waits and re-checks the permission so the user can grant it any
        time via the dashboard without restarting.
        """
        try:
            import cv2
        except Exception:
            self._dashboard_event("Camera capture unavailable (opencv not installed)")
            return
        cap = None
        camera_opened = False
        while not self._stop_requested:
            try:
                if not camera_opened:
                    if not self._request_permission("camera", blocking=False):
                        time.sleep(5.0)
                        continue
                    cap = cv2.VideoCapture(0)
                    if not cap.isOpened():
                        self._dashboard_event("No camera detected or access denied")
                        cap.release()
                        cap = None
                        time.sleep(15.0)
                        continue
                    camera_opened = True
                    self._dashboard_event("Camera capture started")
                ret, frame = cap.read()
                if not ret:
                    self._dashboard_event("Camera frame read failed")
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    camera_opened = False
                    time.sleep(5.0)
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                mean = float(gray.mean())
                if mean < 10:
                    self._dashboard_event("Camera frame very dark")
                time.sleep(1.0)
            except Exception as exc:
                print("Camera monitor failed:", exc)
                self._dashboard_event("Camera monitor encountered an error")
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                cap = None
                camera_opened = False
                time.sleep(10.0)
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

    def _proactive_alert_loop(self) -> None:
        """Simple proactive alerting loop.

        Checks dashboard state periodically and speaks brief alerts for high-priority conditions.
        """
        from datetime import datetime, timedelta
        while True:
            try:
                state = self.dashboard.get_state() if self.dashboard else {}
                lead_count = len(state.get("leads", [])) if isinstance(state.get("leads", []), list) else 0
                last_saved_str = state.get("last_saved") or ""
                last_saved = None
                if last_saved_str:
                    try:
                        last_saved = datetime.strptime(last_saved_str, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        last_saved = None

                # Alert: many leads found but not recently saved
                if lead_count >= 6:
                    if not last_saved or (datetime.now() - last_saved) > timedelta(minutes=10):
                        msg = f"Attention: {lead_count} leads found and not saved recently. Consider saving or reviewing." 
                        self._dashboard_event("Proactive alert: unsaved leads")
                        self.speak(msg)

                # Alert: no saves in last 24 hours
                if last_saved and (datetime.now() - last_saved) > timedelta(hours=24):
                    self._dashboard_event("Proactive alert: no backups in 24 hours")
                    self.speak("Reminder: you have not backed up leads in the last 24 hours.")

            except Exception as exc:
                print("Proactive alert loop error:", exc)
            time.sleep(60)

    def run(self) -> None:
        """Main assistant loop: listen, detect intent, execute workflow."""
        self.speak("Hello. I am Mark, your business growth assistant.")
        while True:
            if self._stop_requested:
                break
            if not self.is_listening:
                time.sleep(1)
                continue
            query = self.listen()
            if not query:
                continue
            if any(phrase in query.lower() for phrase in ["exit", "stop", "shutdown", "quit"]):
                self.speak("Shutting down. I am ready when you need me.")
                break
            self.execute_intent(query)
        self._update_dashboard(status="Stopped", last_action="Assistant stopped")


if __name__ == "__main__":
    assistant = MarkAssistant(load_config())
    try:
        assistant.run()
    finally:
        try:
            if getattr(assistant, "learner", None):
                assistant.learner.shutdown()
        except Exception:
            pass
