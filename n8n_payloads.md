n8n Webhook Payloads for Mark

Overview

This document shows example webhook payloads Mark sends to your n8n webhook (`N8N_WEBHOOK_URL`). Use these payloads as the `POST` body for the primary automation entrypoint. The webhook receiver should branch by `agent` and handle the `payload` object.

Basic cURL example

```bash
curl -X POST "$N8N_WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{"agent":"LeadGenerationAgent","payload":{"query":"urgent react developer roles","context":{}}}'
```

Common fields

- `agent` — string: routing name (e.g., `LeadGenerationAgent`, `ArbitrageLeadAgent`, `ResearchAgent`, `EmailAgent`, `GeneralAgent`).
- `payload` — object: agent-specific content. Keep it small and explicit.
- `context` — optional object: short context snapshot (e.g., `current_query`, `window_title`, `lead_summary`).

Example: Lead Generation

Payload (search + metadata):

```json
{
  "agent": "LeadGenerationAgent",
  "payload": {
    "query": "lead developer for fintech payments",
    "limit": 10,
    "context": {
      "heard_text": "find leads for payments developer",
      "source": "voice"
    }
  }
}
```

n8n flow suggestion:
- Webhook (POST) -> Switch (agent) -> Lead scraping node / HTTP request -> Google Sheets (optional) -> Notify (Slack/Email)

Example: Arbitrage Lead Generation

Payload (includes freelancer network hint):

```json
{
  "agent": "ArbitrageLeadAgent",
  "payload": {
    "query": "urgent react developer job",
    "leads": [],
    "freelancer_hint": "react",
    "context": {"heard_text":"find urgent react roles"}
  }
}
```

n8n flow suggestion:
- Webhook -> Switch(agent) -> Enrich leads (HTTP/HTTP Request to job board) -> Filter high-value leads -> Create proposal (Compose) -> Save to Drive -> Notify

Example: Research Agent (informational)

```json
{
  "agent": "ResearchAgent",
  "payload": {
    "query": "best open-source email deliverability tools",
    "context": {"heard_text":"research deliverability tools"}
  }
}
```

n8n flow suggestion:
- Webhook -> Research node (HTTP/execute script) -> Summarize (AI node or function) -> Return summarized report or create a note in Google Drive

Example: Email Agent (draft/send)

```json
{
  "agent": "EmailAgent",
  "payload": {
    "query": "Email the client the project update and next steps",
    "to": "client@example.com",
    "subject": "Project update",
    "body_hint": "Provide a short status and 3 next steps",
    "context": {}
  }
}
```

n8n flow suggestion:
- Webhook -> Email Draft (Compose) -> (optional) Human approval step -> SMTP node to send

Best practices

- Keep payloads small — send short context snapshots rather than whole files.
- Use `context` to provide only the most relevant bits for fast decision-making.
- Validate incoming payloads in your n8n workflow using a `Switch` or `If` node by `agent` value.
- Return a JSON response from n8n to confirm receipt; Mark currently ignores success body but will log errors if HTTP fails.

Security

- Protect your `N8N_WEBHOOK_URL` (store in `.env` only).
- If your n8n supports it, use a secret header (`X-Mark-SIG`) and validate in your workflow.

Quick test commands

```bash
# Lead generation test
curl -X POST "$N8N_WEBHOOK_URL" -H "Content-Type: application/json" -d '{"agent":"LeadGenerationAgent","payload":{"query":"urgent react"}}'

# Arbitrage test
curl -X POST "$N8N_WEBHOOK_URL" -H "Content-Type: application/json" -d '{"agent":"ArbitrageLeadAgent","payload":{"query":"urgent react","freelancer_hint":"react"}}'
```


License: use these snippets freely in your local n8n workflows.
