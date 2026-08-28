# Mark Business Assistant

This workspace contains a local scaffold for a high-autonomy business assistant named Mark It is designed to:

- Use free local voice output with `pyttsx3`
- Detect business intent and trigger self-hosted automation workflows
- Prioritize lead generation and research workflows
- Save lead data locally to `leads.csv` with optional Google Sheets backup

## Chrome companion

The `mark_chrome_extension` folder contains a Gemini-style floating Mark panel for Chrome. It opens with **Alt+M**, can send selected text or a short page summary only after you press Send, and connects to Mark's local dashboard API. Follow the installation and AI-workflow setup in [mark_chrome_extension/README.md](mark_chrome_extension/README.md).

## Jarvis-style voice, chat, and link learning

Run `start_mark.bat` and open `http://localhost:8080`. The dashboard provides:

- Voice recognition through the existing microphone controls when a Windows microphone is available.
- **Talk to Mark**, a text-chat option that includes relevant saved link notes with each AI workflow request.
- **Teach Mark a Link**, which extracts readable text only from a public `http/https` page you submit and stores a local JSON knowledge note in `knowledge/`.
- **Open a browser and search the web**, so if Mark is unsure, it can look up information directly and verify it, instead of guessing.
- **Language switching**, including `language:en`, `language:hi`, and `language:gu` for English, Hindi, and Gujarati voice interactions.

Set `MARK_CHAT_WEBHOOK_URL` in `.env` to connect the chat to your n8n/AI workflow. Mark sends the question, optional page context, and up to three relevant saved source excerpts; the workflow should return JSON with `reply`, `message`, or `answer`. If the workflow is missing or unavailable, Mark will automatically fall back to direct DuckDuckGo web research and will open a browser for the relevant task.

## Files

- `Mark.py`: Assistant entrypoint and workflow dispatcher
- `requirements.txt`: Python dependency hints
- `.env.example`: Example environment variables for API keys and integration settings

## Setup

1. Copy `.env.example` to `.env`.
2. Fill in your webhook URL and optional Google Sheets settings.
3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Run the assistant:

```bash
python Mark.py
```

### Optional desktop shortcut

1. Run `python create_shortcut.py` once.
2. A shortcut named `Start Mark.lnk` will be created on your Windows desktop.
3. Double-click it to launch the assistant.

## Accessing the Mark Dashboard

1. Open a browser on the same machine and go to `http://localhost:8080/`.
2. To view the dashboard from another device on your local network, find your PC IP address and open:
   `http://<PC_IP_ADDRESS>:8080/`
3. The assistant uses the same dashboard host setting so the page can be opened from anywhere on your LAN.
4. The dashboard updates automatically every few seconds and shows:
   - spoken text as "Heard text"
   - intent routing
   - lead count and recent leads
   - Drive upload and n8n status

> If your PC firewall blocks port `8080`, allow it or choose another open port in `dashboard.py`.

## n8n Workflow Setup

1. Install and open n8n.
2. Create a new workflow with a `Webhook` node.
3. Configure the webhook to accept `POST` requests.
4. Add an `Agent` or `AI` node to represent the system prompt for business goals.
   - Example prompt: `You are Mark, a professional, loyal, proactive business assistant focused on lead generation, research, and email automation.`
5. Add `Call Workflow` nodes for:
   - `Email Agent`
   - `Research Agent`
   - `Lead Scoring Agent`
6. Connect the webhook node to the agent node, then route the agent node output to the call workflow nodes.
7. Use the webhook URL in `.env` as `N8N_WEBHOOK_URL`.

## Intent Routing

`Mark.py` detects keywords and sends these agent names to n8n:

- `arbitrage_lead_generation` → `ArbitrageLeadAgent`
- `read_screen_agent` → local screen reader + summary
- `lead_generation` → `LeadGenerationAgent`
- `research_agent` → `ResearchAgent`
- `email_agent` → `EmailAgent`
- default → `GeneralAgent`

## Screen Reader Voice Control

Mark can now use voice commands to read what is on your screen and clipboard.

Say one of these:

- "Read screen"
- "Read this"
- "Screen reader"
- "Read clipboard"

Mark will speak:

- the active window title
- clipboard text if available
- text extracted from the visible screen image
- a short dashboard summary

### Enabling full screen OCR

For full screen reading, install these extra dependencies:

```bash
pip install Pillow pytesseract
```

On Windows, you also need the Tesseract OCR engine:

1. Download the installer from https://github.com/tesseract-ocr/tesseract/releases
2. Install it to the default location
3. Restart Mark

If Tesseract is not installed, Mark will still read clipboard text and dashboard summaries.

## Multimodal hooks and proactive alerts

- Mark can optionally monitor a connected camera for basic presence/brightness signals (requires `opencv-python`).
- A proactive alert loop runs in the background and will speak brief confirmations for high-priority conditions (for example: many unsaved leads, or no backups in 24 hours).

To enable camera capture and alerts, install extra dependencies:

```bash
pip install -r requirements.txt
```

If `opencv-python` is not installed, camera hooks silently disable and the assistant continues to work.

## Autonomy and automation

- You set autonomy via `.env` or env var `AUTONOMY_LEVEL` (`manual` | `quick-confirm` | `auto`).
- Allowlist: list allowed actions in `.env` as `ALLOWLIST=create_proposal,send_email,browser_fill` to restrict what Mark can do automatically.
- To enable full automation, install the optional automation tools and grant desktop control:

```bash
pip install -r requirements.txt
python -m playwright install
```

Security: Full `auto` mode allows the assistant to perform file moves, browser fills, and trigger workflows without asking. Use allowlist to limit actions.

This flow is designed to help you make money quickly by:

1. Searching for urgent React developer job posts and similar high-demand roles.
2. Matching those opportunities with your local freelancer network in `freelancer_network.csv`.
3. Drafting a professional proposal for you to approve.
4. Uploading the proposal to Google Drive and saving the deal locally.

To use it, say something like:

- "Find urgent React developer leads"
- "Start arbitrage lead generation"
- "Scan job boards for urgent React roles"

## Notes

- Voice output is local only via `pyttsx3`.
- Lead search uses free DuckDuckGo scraping.
- `GOOGLE_SHEET_ID` and `GOOGLE_CREDENTIALS_PATH` are used to append lead rows to Google Sheets if configured.
- `GOOGLE_DRIVE_FOLDER_ID` is used to upload fallback files such as `leads.csv` into your Drive folder.
- If an external integration is unavailable, the assistant saves data locally and falls back gracefully.
