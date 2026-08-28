const stateUrl = "/api/state";
const seenEvents = new Set();
let receivedInitialState = false;

async function fetchState() {
  try {
    const response = await fetch(stateUrl, { cache: "no-cache" });
    const data = await response.json();
    updateState(data);
  } catch (error) {
    console.error("Failed to load state:", error);
  }
}

function updateState(data) {
  document.getElementById("status").textContent = data.status || "Idle";
  document.getElementById("current_query").textContent = data.current_query || "None";
  document.getElementById("intent").textContent = data.intent || "None";
  document.getElementById("last_action").textContent = data.last_action || "None";
  document.getElementById("drive_upload").textContent = data.drive_upload || "None";
  document.getElementById("n8n_status").textContent = data.n8n_status || "None";
  document.getElementById("lead_count").textContent = (data.leads || []).length;
  document.getElementById("last_saved").textContent = data.last_saved || "Never";
  updateStartupChecks(data.startup_checks || []);
  updateEvents(data.events || []);
  notifyNewEvents(data.events || []);
  updateLeads(data.leads || []);
}

function showToast(message, kind = "info") {
  const stack = document.getElementById("toast-stack");
  if (!stack || !message) return;
  const toast = document.createElement("div");
  toast.className = `toast ${kind}`;
  toast.textContent = message;
  stack.appendChild(toast);
  window.setTimeout(() => toast.remove(), 6500);
}

function eventKind(message = "") {
  const text = message.toLowerCase();
  if (text.includes("failed") || text.includes("error") || text.includes("blocked")) return "error";
  if (text.includes("unavailable") || text.includes("not configured") || text.includes("paused")) return "warning";
  if (text.includes("ready") || text.includes("completed") || text.includes("uploaded")) return "success";
  return "info";
}

function notifyNewEvents(events) {
  const currentKeys = new Set(events.map(event => `${event.time}|${event.message}`));
  if (!receivedInitialState) {
    receivedInitialState = true;
    events.slice(0, 1).forEach(event => showToast(event.message, eventKind(event.message)));
  } else {
    events.slice().reverse().forEach(event => {
      const key = `${event.time}|${event.message}`;
      if (!seenEvents.has(key)) showToast(event.message, eventKind(event.message));
    });
  }
  seenEvents.clear();
  currentKeys.forEach(key => seenEvents.add(key));
}

function updateEvents(events) {
  const eventsContainer = document.getElementById("events");
  eventsContainer.innerHTML = "";
  events.forEach(event => {
    const card = document.createElement("div");
    card.className = "event-card";
    card.innerHTML = `<div>${event.message}</div><time>${event.time}</time>`;
    eventsContainer.appendChild(card);
  });
}

function updateLeads(leads) {
  const leadsContainer = document.getElementById("lead_list");
  leadsContainer.innerHTML = "";
  leads.slice(0, 10).forEach(lead => {
    const row = document.createElement("div");
    row.className = "lead-row";
    row.innerHTML = `
      <div class="lead-info">
        <strong>${lead.name || "Unnamed"}</strong>
        <span>${lead.website || "No website"}</span>
        <span>${lead.notes || "No notes"}</span>
      </div>
      <time>${lead.email || "No email"}</time>
    `;
    leadsContainer.appendChild(row);
  });
}

function sendCommand(command) {
  fetch("/api/command", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command })
  })
  .then(response => response.json())
  .then(result => {
    if (result.status === "ok") {
      showToast(result.message || "Command sent.", "success");
    } else {
      showToast(result.message || "Command failed.", "error");
    }
  })
  .catch(error => {
    console.error("Command error:", error);
    showToast("Command failed to send.", "error");
  });
}

function updateStartupChecks(checks) {
  const container = document.getElementById("startup_checks");
  if (!container) return;
  container.innerHTML = "";
  checks.forEach(check => {
    const row = document.createElement("div");
    const isReady = check.status === "ready";
    row.className = `check-row ${isReady ? "ready" : "attention"}`;
    const name = document.createElement("span");
    name.textContent = check.name || "Unnamed check";
    const status = document.createElement("strong");
    status.textContent = check.status || "unknown";
    row.append(name, status);
    container.appendChild(row);
  });
}

async function postMark(endpoint, payload) {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || result.status === "error") {
    throw new Error(result.message || "Mark could not complete that request.");
  }
  return result;
}

fetchState();
setInterval(fetchState, 3000);

document.addEventListener("DOMContentLoaded", () => {
  const openHudButton = document.getElementById("open-hud-button");
  const readButton = document.getElementById("read-screen-button");
  const pauseButton = document.getElementById("pause-listening-button");
  const resumeButton = document.getElementById("resume-listening-button");
  const shutdownButton = document.getElementById("shutdown-button");
  const selfTestButton = document.getElementById("self-test-button");

  if (openHudButton) {
    openHudButton.addEventListener("click", () => {
      window.open("/hud", "jarvis-hud", "width=460,height=760,menubar=no,toolbar=no,location=no,status=no");
    });
  }
  if (readButton) {
    readButton.addEventListener("click", () => sendCommand("read_screen"));
  }
  if (pauseButton) {
    pauseButton.addEventListener("click", () => sendCommand("stop_listening"));
  }
  if (resumeButton) {
    resumeButton.addEventListener("click", () => sendCommand("start_listening"));
  }
  if (shutdownButton) {
    shutdownButton.addEventListener("click", () => sendCommand("shutdown"));
  }
  if (selfTestButton) {
    selfTestButton.addEventListener("click", async () => {
      selfTestButton.disabled = true;
      showToast("Running Mark self-test...", "info");
      try {
        const result = await postMark("/api/mark/self-test", {});
        updateStartupChecks(result.checks || []);
        const kind = result.ready_count === result.total_count ? "success" : "warning";
        showToast(result.message || "Self-test completed.", kind);
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        selfTestButton.disabled = false;
      }
    });
  }

  const chatForm = document.getElementById("mark-chat-form");
  const chatMessage = document.getElementById("mark-chat-message");
  const chatReply = document.getElementById("mark-chat-reply");
  chatForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = chatMessage.value.trim();
    if (!message) return;
    chatReply.textContent = "Mark is thinking…";
    try {
      const result = await postMark("/api/mark/chat", { message, context: {} });
      chatReply.textContent = result.reply || "Mark completed the request.";
      showToast("Mark replied to your chat.", "success");
    } catch (error) {
      chatReply.textContent = error.message;
      showToast(error.message, "error");
    }
  });

  const learnForm = document.getElementById("mark-learn-form");
  const learnUrl = document.getElementById("mark-learn-url");
  const learnReply = document.getElementById("mark-learn-reply");
  learnForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const url = learnUrl.value.trim();
    if (!url) return;
    learnReply.textContent = "Mark is reading and indexing the link…";
    try {
      const result = await postMark("/api/mark/learn", { url });
      learnReply.textContent = result.message || "Mark learned the link.";
      learnForm.reset();
      showToast(result.message || "Mark learned the link.", "success");
    } catch (error) {
      learnReply.textContent = error.message;
      showToast(error.message, "error");
    }
  });
});
