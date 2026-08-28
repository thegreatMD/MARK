# Mark Chrome Companion

This extension adds a floating Mark button to ordinary web pages. It uses the local Mark server at `http://localhost:8080` by default.

## Install in Chrome

1. Start Mark with `start_mark.bat` (or run `python Mark.py`).
2. Open `chrome://extensions`.
3. Enable **Developer mode**.
4. Choose **Load unpacked** and select this `mark_chrome_extension` folder.
5. Open any normal website and press **Alt+M** or click the blue **M**.

Mark sends no page content until you press **Send**. With the context checkbox on, it shares selected text first; if nothing is selected, it shares a maximum of 12,000 characters from visible page text.

## Connect an AI workflow

Add this to the project `.env` file:

```env
MARK_CHAT_WEBHOOK_URL=https://your-n8n-or-ai-webhook.example/mark-chat
MARK_API_TOKEN=choose-a-long-secret
```

The webhook receives `message` and `context` (`url`, `title`, `selectedText`, `pageText`) and should return JSON with a `reply`, `message`, or `answer` field. In Chrome, open **Details → Extension options** and enter the same API token.

For access on another device, host the Mark API behind HTTPS with authentication; do not expose port 8080 directly to the public internet.
