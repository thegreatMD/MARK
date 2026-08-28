(() => {
  if (window.top !== window || document.getElementById("mark-web-companion")) return;
  const host = document.createElement("div");
  host.id = "mark-web-companion";
  host.attachShadow({ mode: "open" });
  const root = host.shadowRoot;
  root.innerHTML = `
    <button class="mark-launcher" aria-label="Open Mark" title="Open Mark (Alt+M)">M</button>
    <section class="mark-panel" aria-label="Mark web companion" hidden>
      <header><strong>Mark</strong><span>Page companion</span><button class="mark-close" aria-label="Close">×</button></header>
      <main><p class="mark-status">Ask Mark about this page.</p>
        <label class="mark-context"><input type="checkbox" checked> Include selected text or page summary</label>
        <textarea maxlength="4000" placeholder="Ask Mark anything about this page…" aria-label="Message for Mark"></textarea>
        <button class="mark-send">Send to Mark</button><div class="mark-reply" aria-live="polite"></div>
      </main><footer>Your page content is shared only when you press Send.</footer>
    </section>`;
  const panel = root.querySelector(".mark-panel");
  const launcher = root.querySelector(".mark-launcher");
  const input = root.querySelector("textarea");
  const includeContext = root.querySelector("input");
  const send = root.querySelector(".mark-send");
  const reply = root.querySelector(".mark-reply");
  const status = root.querySelector(".mark-status");
  function toggle(forceOpen) { panel.hidden = forceOpen === undefined ? !panel.hidden : !forceOpen; if (!panel.hidden) input.focus(); }
  function getContext() {
    if (!includeContext.checked) return {};
    const selectedText = window.getSelection()?.toString().trim().slice(0, 12000) || "";
    const pageText = selectedText ? "" : (document.body?.innerText || "").trim().slice(0, 12000);
    return { url: location.href, title: document.title, selectedText, pageText };
  }
  async function submit() {
    const message = input.value.trim();
    if (!message) { status.textContent = "Write a message first."; input.focus(); return; }
    send.disabled = true; status.textContent = "Mark is thinking…"; reply.textContent = "";
    const response = await chrome.runtime.sendMessage({ type: "MARK_CHAT", payload: { message, context: getContext() } });
    send.disabled = false;
    if (response?.ok) { status.textContent = "Mark replied"; reply.textContent = response.reply; }
    else { status.textContent = "Connection problem"; reply.textContent = response?.error || "Could not reach Mark. Start Mark.py and check the extension settings."; }
  }
  launcher.addEventListener("click", () => toggle());
  root.querySelector(".mark-close").addEventListener("click", () => toggle(false));
  send.addEventListener("click", submit);
  input.addEventListener("keydown", (event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) submit(); });
  chrome.runtime.onMessage.addListener((message) => { if (message.type === "MARK_TOGGLE") toggle(); });
})();
