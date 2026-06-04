# Switchboard

A cost-aware LLM router with a built-in dashboard. Drop it in front of your app, send OpenAI-shaped requests, and stop worrying about which provider to use, what it costs, or what to do when one of them goes down.

## The problem

Most apps using LLMs today aren't sticking to one provider. You probably have an OpenAI key, an Anthropic or Gemini key, maybe Groq for cheap fast inference, and Ollama for the things you'd rather keep on-device. Every team I've talked to ends up in roughly the same place: a thin Python file that picks a provider based on some half-written rules, a spreadsheet somewhere with last month's spend, and a quiet feeling that nobody actually knows where the money is going.

The reason isn't that the problem is hard. The reason is that the tools for it pull you in two directions. On one side you have proxies like LiteLLM and OpenRouter — they unify the API surface, which is genuinely useful, but they don't really *route*. They send your request to whatever model you named and hand back the response. They have no opinion about whether a one-line rewrite should hit GPT-4 or a 1B local model. On the other side you have research projects like RouteLLM that take routing seriously, but you don't really run those in production.

What's missing is the thing in the middle. Something that looks at the request and decides what kind of task it is, picks a provider from a YAML file you actually control, falls back when one provider rate-limits you, and shows you, in real time, what each call cost and what each candidate model *would have* cost. That's what this is.

## What it does

A request comes in on `POST /v1/chat/completions`. The classifier decides this is a `summarization` task. The decision engine reads `configs/config.yaml`, finds the rule for that task type, and picks a provider + model — plus an ordered list of fallbacks if the primary fails. If the rule says `prefer: cheapest`, the candidates get reordered by expected USD cost before picking the primary. The response comes back in OpenAI's normal shape with two extra blocks: `routing` (what was chosen and why) and `cost` (actual USD spent). A row goes into a local SQLite database; the dashboard reads from it.

## Quick start

### With Docker

```bash
docker run -p 8000:8000 \
  -e GROQ_API_KEY=$GROQ_API_KEY \
  -e GEMINI_API_KEY=$GEMINI_API_KEY \
  ghcr.io/<your-username>/switchboard:latest
```

Open `http://localhost:8000` — you land on the dashboard.

### With docker-compose (router + local Ollama)

```bash
git clone https://github.com/<your-username>/switchboard
cd switchboard
cp apps/server/.env.example apps/server/.env   # add your keys
docker compose --profile with-ollama up
```

### From source

```bash
git clone https://github.com/<your-username>/switchboard
cd switchboard
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                # add ",embeddings" for the hybrid classifier
cp apps/server/.env.example apps/server/.env

cd apps/server
PYTHONPATH=../../packages:./ uvicorn app.main:app --reload
```

## How a request flows

Rules live in `configs/config.yaml`. Here's a small slice:

```yaml
routing:
  rules:
    - name: rewrite_to_groq_small
      when: { task_type: rewrite }
      use:  { provider: groq, model: llama-3.1-8b-instant }

    - name: reasoning_cheapest
      when: { task_type: reasoning }
      use:       { provider: groq, model: llama-3.3-70b-versatile }
      fallbacks:
        - { provider: ollama, model: llama3.2:1b }
      prefer: cheapest
      max_cost_per_call: 0.01
      cache: true
```

Send a request:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"please rewrite this email politely"}]}'
```

The response is OpenAI-shaped with two extra blocks:

```jsonc
{
  "id": "chatcmpl-...",
  "model": "llama-3.1-8b-instant",
  "choices": [{ "message": { "role": "assistant", "content": "..." }, ... }],
  "usage":   { "prompt_tokens": 43, "completion_tokens": 13, "total_tokens": 56 },
  "routing": {
    "task_type": "rewrite",
    "selected_provider": "groq",
    "selected_model": "llama-3.1-8b-instant",
    "reason": "Matched routing rule: rewrite_to_groq_small",
    "attempts": [{ "provider": "groq", "model": "llama-3.1-8b-instant", "status": "succeeded", ... }]
  },
  "cost": {
    "currency": "USD",
    "estimated_usd": 0.00000319,
    "input_rate_per_million": 0.05,
    "output_rate_per_million": 0.08,
    "pricing_known": true
  }
}
```

Want to know what something would cost before sending it? Hit `/v1/chat/estimate` with the same body and get a USD min/max per priced model. The dashboard's playground page does this live as you type.

Want to *see every model's answer side by side*? `POST /v1/chat/compare` runs the same prompt against every priced model in parallel and returns each response with its cost and latency. The Playground page has a **Compare all** button that calls this and tints the cheapest answer green, the fastest blue.

Want to debug a routing rule without burning tokens? `POST /v1/chat/route` returns the routing decision (task type, selected provider/model, fallback chain) with no upstream call.

For streaming, add `"stream": true`. You get standard OpenAI SSE chunks followed by a `data: {"x_smart_router_meta": true, "routing": {...}, "cost": {...}}` event, then `data: [DONE]`. Standard OpenAI SDKs ignore the metadata event and keep working unchanged.

## The dashboard

| Page | What's there |
|---|---|
| `/dashboard` | KPIs, cost-by-provider chart, task-type breakdown, recent calls |
| `/dashboard/playground` | Live cost preview per model as you type, then run with streaming |
| `/dashboard/requests` | Full audit log: request ID, provider, model, tokens, USD, attempts |
| `/dashboard/cost` | 7-day spend, daily chart, by-provider and by-model tables |

Server-rendered Jinja templates, Tailwind via CDN, Chart.js for charts. No build step.

## What's in the box

**Providers:** Mock, Groq, Gemini, OpenAI, and Ollama. The three OpenAI-compatible cloud providers share a streaming helper at `packages/router/providers/_openai_compat.py`, so adding another OpenAI-shaped one (Together, Fireworks, …) is about thirty lines. Ollama is implemented separately because its streaming format is JSONL rather than SSE.

**Cost engine:** Pricing lives in `configs/pricing.yaml` and is reloaded at startup. Every successful call gets a USD breakdown. The preview endpoint uses a rule-of-thumb token estimator (max of `chars/4` and `words × 1.3`) so you don't burn tokens to find out what they'd cost. Not exact, but right order of magnitude.

**Classifier:** Two layers used in order. Hand-curated keyword rules first — fast, deterministic, no model loading. If nothing matches, the optional embeddings classifier kicks in: it embeds the prompt and the prototype examples per task type and picks the closest. The second layer needs the `embeddings` extra (`pip install ".[embeddings]"`); the first works on its own.

**Fallback chain:** Each rule can list fallback `(provider, model)` pairs after `use:`. On retryable errors (rate limit, 5xx, network) the next candidate is tried. 4xx errors aren't retried — a malformed request won't succeed elsewhere either. Streaming doesn't fall back mid-response (that would corrupt output).

**Semantic cache:** Per-rule opt-in via `cache: true`. Embeds the prompt and looks for one above a cosine-similarity threshold for the same model. Hit → return cached response, no provider call. In-memory, LRU eviction at 256 entries. Replace with sqlite-vss or a real vector store before scaling horizontally.

**Auth:** Disabled by default. Set `API_TOKEN` and `/v1/*` paths require `Authorization: Bearer <token>`. The dashboard stays open — put it behind a reverse proxy if you need to lock it down.

## Configuration

Three files do all the work and are meant to be edited.

- **`configs/config.yaml`** — routing rules. Every rule has `when:` (task type to match), `use:` (primary provider+model), and optional `fallbacks:`, `prefer:`, `max_cost_per_call:`, `cache:`.
- **`configs/pricing.yaml`** — USD per million tokens, split by input/output. Use `"*"` as the model key for a provider-wide default (Ollama uses this).
- **`apps/server/.env`** — secrets and feature flags. Copy from `.env.example`. Keys needed only for providers you route to. Set `ENABLE_EMBEDDING_CLASSIFIER=true` and `ENABLE_SEMANTIC_CACHE=true` to opt in to the embedding-backed features (requires the `embeddings` extra).

### Security-relevant settings

These are off by default for local development. Set them before exposing Switchboard beyond `localhost`. See [docs/security.md](docs/security.md) and [docs/deployment.md](docs/deployment.md) for full guidance.

| Variable | Default | Purpose |
|---|---|---|
| `API_TOKEN` | _(empty)_ | When set, requires `Authorization: Bearer <token>` on `/v1/*`. |
| `DASHBOARD_AUTH` | `false` | When `true` (and `API_TOKEN` is set), `/dashboard/*` and `/v1/cache/stats` also require auth. Browsers can use HTTP Basic auth — password is the API token. |
| `MAX_DAILY_USD` | `0` | Spend circuit-breaker. When the day's recorded USD spend crosses this, `/v1/chat/{completions,compare}` returns 503 until the next UTC day. `0` disables the cap. |
| `ALLOW_CLIENT_MODEL_OVERRIDE` | `false` | When `false`, requests carrying a `model:` field are rejected with 400. The routing policy decides the model. |

## API surface

| Method · path | Purpose |
|---|---|
| `POST /v1/chat/completions` | OpenAI-compatible chat (JSON or SSE) |
| `POST /v1/chat/estimate` | Cost preview across every priced model |
| `POST /v1/chat/compare` | Run a prompt across N models in parallel; returns each answer + cost + latency |
| `POST /v1/chat/route` | Show the routing decision without calling any provider |
| `GET  /v1/cache/stats` | Semantic cache hits/misses/entries |
| `GET  /health` | Liveness probe |
| `GET  /` | Redirect to the dashboard |
| `GET  /dashboard{,/playground,/requests,/cost}` | Web UI |
| `GET  /docs` | Auto-generated Swagger UI |

## Testing

```bash
pytest                       # 84 tests, ~1s, fully offline
python -m router.eval --cases evals/example.yaml
```

The suite uses `TestClient` plus the `MockProvider` — no API keys needed. The eval harness loads YAML test cases (prompt → expected task type + provider/model) and exits with a CI-friendly status code.

## Recipes

Don't want to write a routing config from scratch? Drop one of the prebaked recipes in `configs/recipes/`:

| Recipe | Wedge | Needs |
|---|---|---|
| `cost-aggressive.yaml` | Local Ollama for anything plausible; small Groq for the rest | Ollama running + `GROQ_API_KEY` |
| `quality-first.yaml` | Premium model per task. No penny-pinching | `OPENAI_API_KEY` (preferred) or `GROQ_API_KEY` |
| `local-first.yaml` | Try local first; cloud only on failure. Privacy / offline | Ollama running |
| `balanced.yaml` | Small/cheap by default with automatic fallback to bigger models. Best starting point | `GROQ_API_KEY`, `GEMINI_API_KEY` |
| `openclaw-channels.yaml` | Per-channel routing for [OpenClaw](https://github.com/openclaw/openclaw) — WhatsApp/Telegram/iMessage to local Ollama, work Slack to GPT-4o. See [docs/integrations/openclaw.md](docs/integrations/openclaw.md) | Ollama + provider keys; OpenClaw on the front end |

```bash
cp configs/recipes/balanced.yaml configs/config.yaml
# restart the server
```

## Use it from Claude Code / Cursor / Claude Desktop (MCP)

Switchboard ships with an MCP server adapter, so AI coding assistants can offload sub-tasks to your router instead of always burning their own expensive tokens.

```bash
pip install -e ".[mcp]"   # adds the `mcp` extra
```

Then in Claude Desktop's `claude_desktop_config.json` (or the equivalent in Claude Code / Cursor):

```json
{
  "mcpServers": {
    "switchboard": {
      "command": "switchboard-mcp",
      "env": {
        "SWITCHBOARD_BASE_URL": "http://localhost:8000",
        "SWITCHBOARD_API_TOKEN": ""
      }
    }
  }
}
```

Switchboard must be running. The MCP server exposes three tools:

- `route_chat(prompt, max_tokens)` — sends the prompt through Switchboard's routing.
- `estimate_cost(prompt)` — preview cost across every priced model, no tokens spent.
- `get_routing_decision(prompt)` — show what Switchboard would route this to, no upstream call.

So inside Claude Code you can say *"summarize this 5KB file using the cheap router"* and the assistant calls `route_chat` instead of running summarization on its own expensive context.

## Documentation

- [Security guide](docs/security.md) — threat model, what's exposed by default, all the security controls.
- [Deployment guide](docs/deployment.md) — Docker, compose, nginx/Caddy reverse proxies, Kubernetes manifests, hardening checklist.
- [OpenClaw integration](docs/integrations/openclaw.md) — per-channel routing for the OpenClaw personal AI assistant.

## What it doesn't do (yet)

- No Anthropic provider yet — planned, same shape as the other OpenAI-compatible ones.
- The keyword classifier is hand-curated. The embeddings fallback helps but isn't a substitute for thinking about your domain. Prototypes are now editable in `configs/classifier_prototypes.yaml`.
- The semantic cache is in-memory only. Single-instance only until a vector store backend lands. **Do not enable for legal / medical / financial / per-user PII workloads** — see [docs/security.md](docs/security.md).
- Cost preview uses a token estimate (off by maybe 10-15% vs the real tokenizer). Good for routing decisions, not for billing or finance reports. Actual response cost uses the provider-reported token count and is exact.
- Streaming has no fallback mid-response. If the first chunk lands and the connection drops, the stream aborts. (Non-streaming requests fall back cleanly.)
- No per-IP rate limiting or multi-tenant auth. The spend circuit-breaker (`MAX_DAILY_USD`) protects against runaway cost; per-IP/per-token rate limiting belongs in a reverse proxy.
- Single-instance only. Both the semantic cache and the persistent request log are local; running >1 replica gives you fragmented state until both are externalised.

## Roadmap

More providers (Anthropic first), a real vector-store backend for the cache, rate limiting, a plugin system so the community can add providers without forking, and at some point a TypeScript SDK.

## Contributing

PRs welcome. Open an issue first for anything beyond a typo so we can talk through the approach.

```bash
pip install -e ".[dev,embeddings]"
pytest
ruff check . && black --check .
```

## License

MIT. See [LICENSE](LICENSE).
