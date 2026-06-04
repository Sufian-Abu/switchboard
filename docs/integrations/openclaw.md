# OpenClaw + Switchboard

[OpenClaw](https://github.com/openclaw/openclaw) is a self-hosted personal AI assistant. It talks to you on 20+ channels (WhatsApp, Slack, Telegram, iMessage, Signal, Discord, …) and forwards your messages to an LLM backend you configure.

Switchboard sits behind it. Same idea as Stripe sitting behind a checkout page: OpenClaw owns the conversation, Switchboard owns the LLM bill. You point OpenClaw's LLM backend at Switchboard once, and from then on every message gets routed by task type, by channel, and by your YAML rules. Costs show up on the Switchboard dashboard. Nothing about how OpenClaw works visibly changes.

## What you get out of it

- **Per-channel routing.** WhatsApp messages to mom go to free local Ollama. Work Slack hits GPT-4o. Telegram bots run on Groq llama-8b. You decide in YAML.
- **One bill, transparent.** OpenClaw forwards everything to Switchboard. Switchboard records every call to SQLite with cost in USD. The dashboard shows you exactly what each channel cost this week.
- **Fallback for free.** A provider 429s? Switchboard transparently tries the next candidate from the rule. OpenClaw keeps working.
- **Privacy by routing.** Sensitive channels (Signal, iMessage, work Slack) can be configured to *only* hit local Ollama. They never leave your machine.

No code changes in OpenClaw. No code changes in Switchboard either — this is a pure config-time integration.

## Setup (5 minutes)

### 1. Run Switchboard

```bash
git clone https://github.com/Sufian-Abu/switchboard
cd switchboard
cp apps/server/.env.example apps/server/.env  # add provider keys
docker compose up
```

Server is on `http://localhost:8000`. Dashboard at `/dashboard`, Swagger at `/docs`.

### 2. Drop in the OpenClaw recipe

```bash
cp configs/recipes/openclaw-channels.yaml configs/config.yaml
# restart the server
```

This recipe routes:

- WhatsApp / Telegram / Signal / iMessage → local Ollama (free, private)
- Voice (mobile) → Groq small (fast)
- Discord → Groq small with Ollama fallback
- Slack → Groq small (or GPT-4o when `metadata.workspace = work`)
- Anything else → task-type fallback

Edit it to match your channels — it's just YAML.

### 3. Point OpenClaw at Switchboard

In OpenClaw's onboarding wizard (or its config), set the LLM backend to:

- **Base URL:** `http://localhost:8000/v1`
- **API key:** whatever you set as `API_TOKEN` in Switchboard's `.env` (or anything if you left auth off for local use)

OpenClaw will now send chat completion requests through Switchboard.

### 4. Tag requests with channel metadata

For per-channel routing to fire, OpenClaw needs to attach the channel name in the request's `metadata` field. The shape Switchboard expects:

```json
{
  "messages": [...],
  "metadata": { "channel": "whatsapp", "workspace": "work" }
}
```

The `metadata` field is part of the OpenAI extension surface — most OpenAI-compatible servers ignore it; Switchboard reads it. Wire whichever fields make sense for your channels.

If you don't want to add metadata fields, that's fine too — the recipe's task-type rules at the bottom kick in and route by *what the message is about* (rewrite vs summarization vs reasoning) instead of *where it came from*.

## How the routing actually works

Each request flows through Switchboard like this:

1. **Classifier** looks at the message text and labels the task type (`rewrite`, `summarization`, `structured_extraction`, `reasoning`, `general_chat`).
2. **Decision engine** scans your YAML rules top to bottom. For each rule, the `when:` clause must match — every key in `when:` is ANDed:
   - `task_type: rewrite` matches if the classifier said rewrite
   - `metadata.channel: whatsapp` matches if the request carried that metadata
   - Combined: `{task_type: reasoning, metadata.channel: slack}` matches only if both hold
3. **Provider call** with fallback chain on retryable errors.
4. **Response** is OpenAI-shaped plus a `routing` block (what was chosen, why, full attempt chain) and a `cost` block (USD breakdown).

OpenClaw sees a normal OpenAI response and doesn't need to know any of this. The extra blocks are invisible to clients that don't look for them.

## What it costs you

Switchboard is MIT-licensed, fully self-hosted, no markup. You pay the providers directly with your own API keys. Local Ollama routes cost $0 (you're paying for electricity instead). The Switchboard dashboard runs on your machine; nobody else sees your traffic.

## Examples

### Cost breakdown after a week of mixed usage

Hypothetical 7-day window on the dashboard's Cost page:

| Channel | Calls | Tokens | USD |
|---|---|---|---|
| `whatsapp` | 412 | 38,210 | $0.00 (Ollama) |
| `signal` | 87 | 9,540 | $0.00 (Ollama) |
| `slack/work` | 23 | 11,002 | $0.14 (GPT-4o) |
| `slack/personal` | 156 | 14,830 | $0.0009 (Groq small) |
| `telegram` | 31 | 2,810 | $0.00 (Ollama) |

The work Slack costs more per call because it routes to GPT-4o for reasoning, while everything else stays local. That's the tradeoff you set in the recipe.

### Debugging a routing rule

Hit the route endpoint to preview where a hypothetical request would land:

```bash
curl -X POST http://localhost:8000/v1/chat/route \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"summarize this thread"}],"metadata":{"channel":"slack","workspace":"work"}}'
```

Returns the routing decision — task type, selected provider, selected model, reason — without calling any provider.

## Roadmap

If this integration is useful to you, please open an issue or PR on either repo. Things we're considering:

- A built-in OpenClaw skill that calls Switchboard's MCP server directly, so OpenClaw can ask "what would this prompt cost?" before sending.
- Per-conversation cost summaries surfaced back into OpenClaw's channel UI.
- Shared dashboard view filtered to OpenClaw traffic.

## Questions / feedback

- Switchboard: [github.com/Sufian-Abu/switchboard](https://github.com/Sufian-Abu/switchboard)
- OpenClaw: [github.com/openclaw/openclaw](https://github.com/openclaw/openclaw)
