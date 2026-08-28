import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from flask import Flask, jsonify, render_template, request


class DashboardState:
    def __init__(self):
        self.state = {
            "status": "Starting",
            "current_query": "",
            "intent": "",
            "last_action": "",
            "leads": [],
            "events": [],
            "last_saved": "",
            "drive_upload": "",
            "n8n_status": "",
            "startup_checks": [],
            "permissions": {},
            "pending_permissions": [],
            "learner_stats": {},
            "learner_top_patterns": {},
            "assistant_voice": "",
            "listening": True,
        }

    def update(self, **kwargs):
        for key, value in kwargs.items():
            if key in self.state:
                self.state[key] = value
        self._append_event(kwargs)

    def append_event(self, message: str):
        self.state["events"].insert(0, {
            "time": self._now(),
            "message": message,
        })
        self.state["events"] = self.state["events"][:50]

    def add_leads(self, leads):
        self.state["leads"] = leads
        self.append_event(f"Lead list updated ({len(leads)} items)")

    def _append_event(self, kwargs):
        if not kwargs:
            return
        message = ", ".join(f"{k}: {v}" for k, v in kwargs.items() if v is not None and k != "events")
        if message:
            self.append_event(message)

    def to_dict(self):
        return self.state.copy()

    @staticmethod
    def _now():
        from datetime import datetime
        return datetime.now().strftime("%H:%M:%S")


class DashboardServer:
    def __init__(
        self,
        port: int = 8080,
        command_handler: Optional[Callable[[str], dict]] = None,
        chat_handler: Optional[Callable[[Dict[str, Any]], dict]] = None,
        learn_handler: Optional[Callable[[Dict[str, Any]], dict]] = None,
        self_test_handler: Optional[Callable[[Dict[str, Any]], dict]] = None,
        permission_manager: Optional[Any] = None,
        permission_set_handler: Optional[Callable[[str, str], bool]] = None,
        command_learner: Optional[Any] = None,
    ):
        self.state = DashboardState()
        self.command_handler = command_handler
        self.chat_handler = chat_handler
        self.learn_handler = learn_handler
        self.self_test_handler = self_test_handler
        self.permission_manager = permission_manager
        self.permission_set_handler = permission_set_handler
        self.command_learner = command_learner
        template_path = Path(__file__).with_name("dashboard_templates")
        static_path = Path(__file__).with_name("dashboard_static")
        self.app = Flask(
            __name__, 
            template_folder=str(template_path),
            static_folder=str(static_path),
            static_url_path="/static"
        )
        self.port = port
        self._configure_routes()

    def _configure_routes(self):
        @self.app.route("/")
        def index():
            return render_template("index.html")

        @self.app.route("/hud")
        def hud():
            return render_template("hud.html")

        @self.app.route("/api/state")
        def get_state():
            return jsonify(self.state.to_dict())

        @self.app.route("/api/command", methods=["POST"])
        def command():
            data = request.get_json(silent=True) or {}
            command_name = data.get("command", "")
            if not command_name:
                return jsonify({"status": "error", "message": "command is required"}), 400
            if not self.command_handler:
                return jsonify({"status": "error", "message": "command handler not configured"}), 500
            result = self.command_handler(command_name)
            return jsonify(result)

        @self.app.route("/api/hud/run", methods=["POST"])
        def hud_run():
            data = request.get_json(silent=True) or {}
            if not isinstance(data, dict):
                return jsonify({"status": "error", "message": "A JSON object is required."}), 400
            if not self.command_handler:
                return jsonify({"status": "error", "message": "HUD handler not configured"}), 500
            feature = str(data.get("feature") or data.get("command") or "").strip()
            if not feature:
                return jsonify({"status": "error", "message": "feature is required"}), 400
            if hasattr(self.command_handler, "__self__") and hasattr(self.command_handler.__self__, "handle_hud_feature"):
                result = self.command_handler.__self__.handle_hud_feature(data)
            else:
                result = self.command_handler(feature)
            return jsonify(result)

        @self.app.route("/api/hud/window", methods=["POST"])
        def hud_window():
            data = request.get_json(silent=True) or {}
            if not isinstance(data, dict):
                return jsonify({"status": "error", "message": "A JSON object is required."}), 400
            action = str(data.get("action") or "").strip().lower()
            owner = getattr(self.command_handler, "__self__", None)
            if owner is None or not hasattr(owner, "handle_hud_window"):
                return jsonify({"status": "error", "message": "HUD window control is not configured."}), 503
            result = owner.handle_hud_window(action)
            return jsonify(result)

        @self.app.route("/api/mark/chat", methods=["POST"])
        def mark_chat():
            """Receive a user-initiated message from the Mark Chrome companion."""
            data = request.get_json(silent=True) or {}
            if not self.chat_handler:
                return jsonify({"status": "error", "message": "Mark chat is not configured."}), 503
            if not isinstance(data, dict):
                return jsonify({"status": "error", "message": "A JSON object is required."}), 400
            result = self.chat_handler(data)
            return jsonify(result)

        @self.app.route("/api/mark/learn", methods=["POST"])
        def mark_learn():
            """Learn a web page deliberately submitted by the Mark user."""
            data = request.get_json(silent=True) or {}
            if not self.learn_handler:
                return jsonify({"status": "error", "message": "Mark learning is not configured."}), 503
            if not isinstance(data, dict):
                return jsonify({"status": "error", "message": "A JSON object is required."}), 400
            result = self.learn_handler(data)
            return jsonify(result)

        @self.app.route("/api/mark/self-test", methods=["POST"])
        def mark_self_test():
            """Run Mark's non-invasive readiness tests on demand."""
            data = request.get_json(silent=True) or {}
            if not self.self_test_handler:
                return jsonify({"status": "error", "message": "Mark self-test is not configured."}), 503
            if not isinstance(data, dict):
                return jsonify({"status": "error", "message": "A JSON object is required."}), 400
            result = self.self_test_handler(data)
            return jsonify(result)

        @self.app.route("/api/permissions")
        def list_permissions():
            """Return the current permission statuses and any pending approval requests."""
            if self.permission_manager is None:
                return jsonify({"status": "error", "message": "Permission manager is not configured."}), 503
            return jsonify({
                "status": "ok",
                "permissions": self.permission_manager.all_statuses(),
                "pending": self.permission_manager.pending_requests(),
            })

        @self.app.route("/api/permissions", methods=["POST"])
        def set_permission():
            """Approve or deny a permission from the dashboard UI."""
            if self.permission_set_handler is None and self.permission_manager is None:
                return jsonify({"status": "error", "message": "Permission manager is not configured."}), 503
            data = request.get_json(silent=True) or {}
            if not isinstance(data, dict):
                return jsonify({"status": "error", "message": "A JSON object is required."}), 400
            permission = str(data.get("permission", "")).strip()
            new_status = str(data.get("status", "")).strip().lower()
            if new_status not in {"granted", "denied", "unknown"}:
                return jsonify({"status": "error", "message": "status must be granted, denied, or unknown."}), 400
            ok = False
            if self.permission_set_handler:
                try:
                    ok = bool(self.permission_set_handler(permission, new_status))
                except Exception:
                    ok = False
            if not ok and self.permission_manager is not None:
                ok = self.permission_manager.set_status(permission, new_status)
            if not ok:
                return jsonify({"status": "error", "message": f"Could not update permission '{permission}'."}), 400
            return jsonify({"status": "ok", "permission": permission, "new_status": new_status})

        @self.app.route("/api/permissions/reset", methods=["POST"])
        def reset_permissions():
            """Reset all permissions to 'unknown' so the user will be prompted again."""
            if self.permission_manager is None:
                return jsonify({"status": "error", "message": "Permission manager is not configured."}), 503
            self.permission_manager.reset_all()
            return jsonify({"status": "ok", "message": "All permissions have been reset."})

        @self.app.route("/api/learner/status")
        def learner_status():
            """Current command-learner stats: rows stored, intents, on-disk size, memory footprint."""
            if self.command_learner is None:
                return jsonify({"status": "error", "message": "Command learner is not configured."}), 503
            stats = self.command_learner.stats()
            top_patterns = self.command_learner.top_patterns(n=15)
            return jsonify({"status": "ok", "stats": stats, "top_patterns": top_patterns})

        @self.app.route("/api/learner/forget", methods=["POST"])
        def learner_forget():
            """Forget either a single intent or all learned commands.

            Body: {"intent": "..."}  to clear a specific intent, or {"all": true} for everything.
            """
            if self.command_learner is None:
                return jsonify({"status": "error", "message": "Command learner is not configured."}), 503
            data = request.get_json(silent=True) or {}
            if not isinstance(data, dict):
                return jsonify({"status": "error", "message": "A JSON object is required."}), 400
            if data.get("all"):
                self.command_learner.forget_all()
                return jsonify({"status": "ok", "message": "All learned commands have been forgotten."})
            intent = str(data.get("intent") or "").strip()
            if not intent:
                return jsonify({"status": "error", "message": "'intent' (string) or 'all' (true) is required."}), 400
            self.command_learner.forget_intent(intent)
            return jsonify({"status": "ok", "message": f"Forgot learned data for intent '{intent}'."})

    def refresh_permissions_state(self) -> None:
        """Push current permission data into the dashboard state so /api/state reflects it."""
        if self.permission_manager is None:
            return
        try:
            self.state.update(
                permissions=self.permission_manager.all_statuses(),
                pending_permissions=self.permission_manager.pending_requests(),
            )
        except Exception:
            pass

    def start(self):
        thread = threading.Thread(target=self._run_server, daemon=True)
        thread.start()
        self.state.update(status=f"Dashboard running on port {self.port}")

    def _run_server(self):
        self.app.run(host="0.0.0.0", port=self.port, debug=False, use_reloader=False)

    def update_state(self, **kwargs):
        self.state.update(**kwargs)

    def add_event(self, message: str):
        self.state.append_event(message)

    def update_leads(self, leads):
        self.state.add_leads(leads)

    def get_state(self):
        return self.state.to_dict()
