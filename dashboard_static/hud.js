const FEATURES = [
  { id: "mic", label: "MIC", glyph: "◈" },
  { id: "pause", label: "PAUSE", glyph: "■" },
  { id: "screen", label: "SCREEN", glyph: "▣" },
  { id: "leads", label: "LEADS", glyph: "◎" },
  { id: "arbitrage", label: "ARB", glyph: "△" },
  { id: "research", label: "SCAN", glyph: "⌕" },
  { id: "email", label: "MAIL", glyph: "✉" },
  { id: "proposal", label: "PITCH", glyph: "☰" },
  { id: "web", label: "WEB", glyph: "⬡" },
  { id: "learn", label: "LEARN", glyph: "+" },
  { id: "test", label: "TEST", glyph: "⚙" },
  { id: "help", label: "HELP", glyph: "?" },
  { id: "dashboard", label: "FULL", glyph: "▦" },
  { id: "shutdown", label: "OFF", glyph: "⏻", danger: true },
];

const NEEDS_INPUT = new Set(["leads", "arbitrage", "research", "email", "proposal", "web", "learn"]);
let pendingFeature = "chat";
let lastVoice = "";

function $(id) {
  return document.getElementById(id);
}

function layoutNodes() {
  const host = $("nodes");
  host.innerHTML = "";
  const rect = host.getBoundingClientRect();
  const cx = rect.width / 2;
  const cy = rect.height / 2;
  const radius = Math.min(cx, cy) - 36;
  FEATURES.forEach((feature, index) => {
    const angle = (Math.PI * 2 * index) / FEATURES.length - Math.PI / 2;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `node${feature.danger ? " danger" : ""}`;
    btn.dataset.feature = feature.id;
    btn.style.left = `${cx + Math.cos(angle) * radius}px`;
    btn.style.top = `${cy + Math.sin(angle) * radius}px`;
    btn.innerHTML = `<span class="glyph">${feature.glyph}</span><span class="label">${feature.label}</span>`;
    btn.addEventListener("click", () => onFeature(feature.id));
    host.appendChild(btn);
  });
}

function setVoice(text) {
  if (!text) return;
  $("voice-line").textContent = text;
  lastVoice = text;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || result.status === "error") {
    throw new Error(result.message || "Request failed");
  }
  return result;
}

async function onFeature(feature) {
  const typed = $("comms-input").value.trim();
  if (NEEDS_INPUT.has(feature) && !typed) {
    pendingFeature = feature;
    const hint = feature === "learn"
      ? "Paste a public URL, then ENGAGE."
      : `Tell me the target for ${feature.toUpperCase()}, then ENGAGE.`;
    setVoice(hint);
    $("comms-input").focus();
    return;
  }
  pendingFeature = feature === "mic" ? "chat" : feature;
  await runFeature(feature, typed);
}

async function runFeature(feature, query) {
  document.querySelectorAll(".node").forEach((node) => node.classList.add("busy"));
  try {
    if (feature === "chat") {
      const result = await postJson("/api/mark/chat", { message: query, context: {} });
      setVoice(result.reply || "Acknowledged.");
      $("comms-input").value = "";
      return;
    }
    if (feature === "learn") {
      const result = await postJson("/api/mark/learn", { url: query });
      setVoice(result.message || result.reply || "Source indexed.");
      $("comms-input").value = "";
      return;
    }
    if (feature === "test") {
      const result = await postJson("/api/mark/self-test", {});
      setVoice(result.message || "Diagnostics complete.");
      return;
    }
    const result = await postJson("/api/hud/run", { feature, query });
    setVoice(result.reply || result.message || "Done, Sir.");
    if (["leads", "research", "email", "proposal", "web", "learn"].includes(feature)) {
      $("comms-input").value = "";
    }
  } catch (error) {
    setVoice(error.message);
  } finally {
    document.querySelectorAll(".node").forEach((node) => node.classList.remove("busy"));
  }
}

async function fetchState() {
  try {
    const response = await fetch("/api/state", { cache: "no-cache" });
    const data = await response.json();
    $("core-status").textContent = (data.status || "ONLINE").slice(0, 18).toUpperCase();
    $("heard").textContent = `Heard: ${data.current_query || "—"}`;
    $("intent").textContent = `Intent: ${data.intent || "—"}`;
    $("leads").textContent = `Leads: ${(data.leads || []).length}`;
    if (data.assistant_voice && data.assistant_voice !== lastVoice) {
      setVoice(data.assistant_voice);
    }
  } catch (error) {
    $("core-status").textContent = "OFFLINE";
  }
}

async function loadPermissions() {
  const panel = $("perm-panel");
  try {
    const response = await fetch("/api/permissions");
    const data = await response.json();
    const perms = data.permissions || {};
    panel.innerHTML = "";
    Object.entries(perms).forEach(([key, status]) => {
      const row = document.createElement("div");
      row.className = "perm-row";
            row.innerHTML = `<span>${key.split("_").join(" ")} · ${status}</span>`;
      const grant = document.createElement("button");
      grant.textContent = "ALLOW";
      grant.addEventListener("click", () => setPermission(key, "granted"));
      const deny = document.createElement("button");
      deny.textContent = "DENY";
      deny.addEventListener("click", () => setPermission(key, "denied"));
      row.append(grant, deny);
      panel.appendChild(row);
    });
  } catch (error) {
    panel.textContent = "Access panel unavailable.";
  }
}

async function setPermission(permission, status) {
  try {
    await postJson("/api/permissions", { permission, status });
    setVoice(`${permission.split("_").join(" ")} ${status}.`);
    loadPermissions();
  } catch (error) {
    setVoice(error.message);
  }
}

async function closeHudWindow() {
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.close_window) {
      await window.pywebview.api.close_window();
    }
  } catch (error) {
    /* continue to HTTP kill */
  }
  try {
    await postJson("/api/hud/window", { action: "close" });
  } catch (error) {
    /* ignore */
  }
  try {
    window.close();
  } catch (error) {
    /* ignore */
  }
}

async function minimizeHudWindow() {
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.minimize_window) {
      await window.pywebview.api.minimize_window();
      return;
    }
  } catch (error) {
    /* fallback */
  }
  try {
    await postJson("/api/hud/window", { action: "minimize" });
  } catch (error) {
    setVoice("Minimize failed.");
  }
}

function setHudFold(collapsed) {
  $("hud").classList.toggle("collapsed", collapsed);
  $("hud").classList.toggle("expanded", !collapsed);
  requestAnimationFrame(layoutNodes);
  const api = window.pywebview && window.pywebview.api;
  if (!api) return;
  try {
    if (collapsed && api.collapse_window) api.collapse_window();
    if (!collapsed && api.expand_window) api.expand_window();
  } catch (error) {
    /* ignore */
  }
}

document.addEventListener("DOMContentLoaded", () => {
  layoutNodes();
  window.addEventListener("resize", layoutNodes);
  $("core").addEventListener("click", () => {
    setHudFold(!$("hud").classList.contains("collapsed"));
  });
  $("min-btn").addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    minimizeHudWindow();
  });
  $("collapse-btn").addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    setHudFold(true);
  });
  $("close-btn").addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    closeHudWindow();
  });
  $("close-window-btn").addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    closeHudWindow();
  });
  $("comms-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const typed = $("comms-input").value.trim();
    if (!typed) return;
    const feature = pendingFeature || "chat";
    await runFeature(feature === "mic" ? "chat" : feature, typed);
    pendingFeature = "chat";
  });
  document.querySelectorAll("[data-lang]").forEach((button) => {
    button.addEventListener("click", () => runFeature(`language:${button.dataset.lang}`, ""));
  });
  $("perm-toggle").addEventListener("click", () => {
    $("perm-panel").classList.toggle("hidden");
    if (!$("perm-panel").classList.contains("hidden")) loadPermissions();
  });
  fetchState();
  setInterval(fetchState, 2000);
});
