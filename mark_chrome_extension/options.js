const defaults = { endpoint: "http://localhost:8080/api/mark/chat", token: "" };
const endpoint = document.getElementById("endpoint");
const token = document.getElementById("token");
const status = document.getElementById("status");
chrome.storage.sync.get(defaults).then((settings) => { endpoint.value = settings.endpoint; token.value = settings.token; });
document.getElementById("save").addEventListener("click", async () => {
  try { new URL(endpoint.value); } catch { status.textContent = "Enter a valid API URL."; return; }
  await chrome.storage.sync.set({ endpoint: endpoint.value.trim(), token: token.value });
  status.textContent = "Saved.";
});
