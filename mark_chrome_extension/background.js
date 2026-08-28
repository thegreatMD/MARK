const DEFAULT_SETTINGS = { endpoint: "http://localhost:8080/api/mark/chat", token: "" };

chrome.runtime.onInstalled.addListener(async () => {
  const current = await chrome.storage.sync.get(DEFAULT_SETTINGS);
  await chrome.storage.sync.set(current);
});

async function toggleInActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.id) chrome.tabs.sendMessage(tab.id, { type: "MARK_TOGGLE" }).catch(() => {});
}
chrome.action.onClicked.addListener(toggleInActiveTab);
chrome.commands.onCommand.addListener((command) => { if (command === "toggle-mark") toggleInActiveTab(); });

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "MARK_CHAT") return;
  (async () => {
    const settings = await chrome.storage.sync.get(DEFAULT_SETTINGS);
    try {
      const response = await fetch(settings.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...message.payload, token: settings.token })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.status === "error") throw new Error(data.message || `Mark returned ${response.status}.`);
      sendResponse({ ok: true, reply: data.reply || "Mark completed the request." });
    } catch (error) {
      sendResponse({ ok: false, error: error.message || "Unable to reach Mark." });
    }
  })();
  return true;
});
