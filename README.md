# Switchboard

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-143%20passing-brightgreen.svg)](#testing)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](#quick-start)

**Automatically send the right request to the right model at the right cost** — with awareness of provider health, remaining budget, and the channel it came from. Drop it in front of your app, send OpenAI-shaped requests, and stop juggling provider keys, spreadsheets, and rate limits.

---

**Jump to:**
[The problem](#the-problem) ·
[What it does](#what-it-does) ·
[Quick start](#quick-start) ·
[How a request flows](#how-a-request-flows) ·
[Dashboard](#the-dashboard) ·
[What's in the box](#whats-in-the-box) ·
[Configuration](#configuration) ·
[API surface](#api-surface) ·
[Recipes](#recipes) ·
[MCP](#use-it-from-claude-code--cursor--claude-desktop-mcp) ·
[Docs](#documentation) ·
[Limitations](#what-it-doesnt-do-yet) ·
[Roadmap](#roadmap)

## The problem

Most apps using LLMs today aren't sticking to one provider. You probably have an OpenAI key, an Anthropic or Gemini key, maybe Groq for cheap fast inference, and Ollama for the things you'd rather keep on-device. Every team I've talked to ends up in roughly the same place: a thin Python file that picks a provider based on some half-written rules, a spreadsheet somewhere with last month's spend, and a quiet feeling that nobody actually knows where the money is going.

The reason isn't that the problem is hard. The reason is that the tools for it pull you in two directions. On one side you have proxies like LiteLLM and OpenRouter — they unify the API surface, which is genuinely useful, but they don't really *route*. They send your request to whatever model you named and hand back the response. They have no opinion about whether a one-line rewrite should hit GPT-4 or a 1B local model. On the other side you have research projects like RouteLLM that take routing seriously, but you don't really run those in production.

What's missing is the thing in the middle. Something that looks at the request and decides what kind of task it is, picks a provider from a YAML file you actually control, falls back when one provider rate-limits you, and shows you, in real time, what each call cost and what each candidate model *would have* cost. That's what this is.

## What it does

A request comes in on `POST /v1/chat/completions`. The classifier decides this is a `summarization` task. The decision engine reads `configs/config.yaml`, finds the rule for that task type, and picks a provider + model — plus an ordered list of fallbacks if the primary fails. If the rule says `prefer: cheapest`, the candidates get reordered by expected USD cost before picking the primary. The response comes back in OpenAI's normal shape with two extra blocks: `routing` (what was chosen and why) and `cost` (actual USD spent). A row goes into a local SQLite database; the dashboard reads from it.

## Quick start

### Docker (one command)

```bash
docker run -p 8000:8000 \
  -e GROQ_API_KEY=$GROQ_API_KEY \
  -e GEMINI_API_KEY=$GEMINI_API_KEY \
  ghcr.io/sufian-abu/switchboard:latest
```

Open `http://localhost:8000` — you land on the dashboard.

### docker-compose (router + local Ollama)

```bash
git clone https://github.com/Sufian-Abu/switchboard
cd switchboard
cp apps/server/.env.example apps/server/.env   # add your keys
docker compose --profile with-ollama up
```

### From source (Python 3.11+)

```bash
git clone https://github.com/Sufian-Abu/switchboard
cd switchboard
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                # add ",embeddings" for the hybrid classifier
cp apps/server/.env.example apps/server/.env

cd apps/server
PYTHONPATH=../../packages:./ uvicorn app.main:app --reload
```

## How a request flows

Rules live in `configs/config.yaml`. A small slice showing several of the differentiating features:

```yaml
routing:
  rules:
    # Plain task-based routing
    - name: rewrite_to_groq_small
      when: { task_type: rewrite }
      use:  { provider: groq, model: llama-3.1-8b-instant }

    # Cost-aware: reorder by USD, cap per-call, opt into cache
    - name: reasoning_cheapest
      when: { task_type: reasoning }
      use:       { provider: groq, model: llama-3.3-70b-versatile }
      fallbacks:
        - { provider: ollama, model: llama3.2:1b }
      prefer: cheapest
      max_cost_per_call: 0.01
      cache: true

    # Health-aware: skip Gemini when its rolling stats go degraded
    - name: summarize_with_health_check
      when: { task_type: summarization }
      use:       { provider: gemini, model: gemini-2.5-flash }
      fallbacks:
        - { provider: groq, model: llama-3.1-8b-instant }
      avoid_if_health: degraded

    # A/B test 80/20 across two providers
    - name: rewrite_ab_test
      when: { task_type: rewrite }
      split:
        - { name: groq_small,  provider: groq,   model: llama-3.1-8b-instant, weight: 80 }
        - { name: gemini_flash, provider: gemini, model: gemini-2.5-flash,    weight: 20 }
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

A few helpers built on the same engine:

- **Cost before you send** — `POST /v1/chat/estimate` returns USD min/max per priced model. The Playground does this live as you type.
- **Side-by-side comparison** — `POST /v1/chat/compare` runs the same prompt across every priced model in parallel. The Playground's **Compare all** button tints the cheapest green and the fastest blue.
- **Routing-only preview** — `POST /v1/chat/route` returns the routing decision without calling any provider, so you can debug rules without burning tokens.
- **Streaming** — add `"stream": true`. You get standard OpenAI SSE chunks, then a `data: {"x_smart_router_meta": true, "routing": {...}, "cost": {...}}` event, then `data: [DONE]`. Standard OpenAI SDKs ignore the metadata event and keep working unchanged.

## The dashboard

| Page | What's there |
|---|---|
| `/dashboard` | Burn-rate card (when MAX_DAILY_USD set), provider-health grid (healthy/degraded/unhealthy bands), task-type breakdown, cost-by-provider chart, recent calls |
| `/dashboard/playground` | Live cost preview per model as you type, then run with streaming, then a **Compare all** button |
| `/dashboard/requests` | Full audit log: request ID, provider, model, tokens, USD, attempts. **Click any row** to see the verbatim routing reason. |
| `/dashboard/cost` | 7-day spend, daily chart, by-provider, by-model, and by-channel tables |
| `/dashboard/ab` | A/B cohort comparison for rules using `split:` — count, tokens, avg/total cost, success rate side by side |

Server-rendered Jinja templates, Tailwind via CDN, Chart.js for charts. No build step. Open by default for local dev; set `DASHBOARD_AUTH=true` to lock it down — see the [security settings](#security-relevant-settings) below.

## What's in the box

**Providers.** Mock, Groq, Gemini, OpenAI, and Ollama. The three OpenAI-compatible cloud providers share a streaming helper at `packages/router/providers/_openai_compat.py`, so adding another OpenAI-shaped one (Together, Fireworks, …) is about thirty lines. Ollama is implemented separately because its streaming format is JSONL rather than SSE.

**Cost engine.** Pricing lives in `configs/pricing.yaml` and is reloaded at startup. Every successful call gets a USD breakdown. The preview endpoint uses a rule-of-thumb token estimator (max of `chars/4` and `words × 1.3`) so you don't burn tokens to find out what they'd cost. Not exact, but right order of magnitude.

**Classifier.** Two layers used in order. Hand-curated keyword rules first — fast, deterministic, no model loading. Rules now live in `configs/classifier_keywords.yaml` so you can tune them for your domain without forking. If nothing matches, the optional embeddings classifier kicks in: it embeds the prompt and the prototype examples per task type and picks the closest. The second layer needs the `embeddings` extra (`pip install ".[embeddings]"`); the first works on its own.

**Fallback chain.** Each rule can list fallback `(provider, model)` pairs after `use:`. On retryable errors (rate limit, 5xx, network) the next candidate is tried. 4xx errors aren't retried — a malformed request won't succeed elsewhere either. Streaming doesn't fall back mid-response (that would corrupt output).

**Cost-aware routing.** Set `prefer: cheapest` on a rule and candidates are reordered by expected USD per call before selection. Set `max_cost_per_call: N` and any candidate above the cap is excluded; if every candidate is excluded the engine moves on to the next rule. Default is *fail closed* — candidates with no pricing entry are excluded under a cap, unless you opt in with `allow_unknown_pricing_under_cap: true` (use only for trusted free providers like local Ollama).

**Provider health-based routing.** Each successful and failed provider call feeds a rolling 5-minute window of error rate and p95 latency. A rule with `avoid_if_health: degraded` or `unhealthy` will *skip* candidates in those bands before sending — not just retry-around them per request. No other OSS LLM router does this preemptively.

**Budget intelligence.** `MAX_DAILY_USD` is the hard cap (503s when reached). `DAILY_SOFT_CAP_PCT` is the *soft* cap: when today's spend crosses the threshold (e.g. 80%), the engine automatically reorders every rule's fallback chain cheapest-first to stretch the budget instead of going dark.

**A/B testing.** A rule can `split:` traffic across named cohorts with weights. The cohort is recorded on every request, and `/dashboard/ab` shows side-by-side cost, success rate, and token totals so you can pick a winner before committing.

**Semantic cache.** Per-rule opt-in via `cache: true`. Embeds the prompt and looks for one above a cosine-similarity threshold (default 0.97) for the same model. Hit → return cached response, no provider call. In-memory, LRU eviction at 256 entries. **Don't enable for legal / medical / financial / per-user PII workloads** — there's no per-tenant scoping yet. See [docs/security.md](docs/security.md).

**Auth.** Off by default. Set `API_TOKEN` and `/v1/*` paths require `Authorization: Bearer <token>`. Set `DASHBOARD_AUTH=true` (with `API_TOKEN`) and `/dashboard/*` plus `/v1/cache/stats` are also locked down. Browsers can authenticate with HTTP Basic auth — password is the API token — so the native browser credential prompt works.

**Spend circuit-breaker.** Set `MAX_DAILY_USD=N` and the day's recorded spend is checked before each `/v1/chat/{completions,compare}` call. When the cap is hit the endpoint returns 503 with a structured error until the next UTC day. Defends against runaway-cost incidents.

**Observability.** Built-in dashboard + `/metrics` Prometheus endpoint (request counts by endpoint/status, provider latency histograms, cumulative cost by provider/model, cache hit/miss counters). The metrics endpoint requires the `metrics` extra.

## Configuration

Five files do all the work and are meant to be edited.

| File | Purpose |
|---|---|
| `configs/config.yaml` | Routing rules. `when:` (task type or `metadata.*` match), `use:`, optional `fallbacks:`, `prefer:`, `max_cost_per_call:`, `allow_unknown_pricing_under_cap:`, `cache:`. |
| `configs/classifier_keywords.yaml` | Keyword rules per task type. Edit to tune classification for your domain. |
| `configs/classifier_prototypes.yaml` | Embedding prototypes per task type. Only used when the `embeddings` extra is installed. |
| `configs/pricing.yaml` | Per-model pricing (USD per 1M tokens, input/output split). `"*"` is a provider-wide default. |
| `apps/server/.env` | Secrets and feature flags. Copy from `.env.example`. |

### Security-relevant settings

These are off by default for local development. Set them before exposing Switchboard beyond `localhost`. See [docs/security.md](docs/security.md) and [docs/deployment.md](docs/deployment.md) for the full guidance.

| Variable | Default | Purpose |
|---|---|---|
| `API_TOKEN` | _(empty)_ | When set, requires `Authorization: Bearer <token>` on `/v1/*`. |
| `DASHBOARD_AUTH` | `false` | When `true` (and `API_TOKEN` is set), `/dashboard/*` and `/v1/cache/stats` also require auth. Browsers can use HTTP Basic auth — password is the API token. |
| `MAX_DAILY_USD` | `0` | Spend circuit-breaker. When the day's recorded USD spend crosses this, `/v1/chat/{completions,compare}` returns 503 until the next UTC day. `0` disables the cap. |
| `DAILY_SOFT_CAP_PCT` | `0.0` | Soft cap as a fraction of `MAX_DAILY_USD` (e.g. `0.8`). When today's spend crosses it, fallback chains are reordered cheapest-first automatically. Active only when `MAX_DAILY_USD > 0`. |
| `ALLOW_CLIENT_MODEL_OVERRIDE` | `false` | When `false`, requests carrying a `model:` field are rejected with 400. The routing policy decides the model. |

## API surface

| Method · path | Purpose |
|---|---|
| `POST /v1/chat/completions` | OpenAI-compatible chat (JSON or SSE) |
| `POST /v1/chat/estimate` | Cost preview across every priced model — no upstream call |
| `POST /v1/chat/compare` | Run a prompt across N models in parallel; returns each answer + cost + latency |
| `POST /v1/chat/route` | Show the routing decision without calling any provider |
| `GET  /v1/cache/stats` | Semantic cache hits/misses/entries |
| `GET  /v1/health/providers` | Per-provider rolling-window health (error rate, p50/p95 latency, band) |
| `GET  /metrics` | Prometheus metrics (requires the `metrics` extra) |
| `GET  /health` | Liveness probe |
| `GET  /` | Redirect to the dashboard |
| `GET  /dashboard{,/playground,/requests,/cost}` | Web UI |
| `GET  /docs` | Auto-generated Swagger UI |

## Testing

```bash
pytest                       # 143 tests, ~1.6s, fully offline
python -m router.eval --cases evals/example.yaml
```

The suite uses FastAPI's `TestClient` plus the `MockProvider` — no API keys needed, no network calls, no flakes. The eval harness loads YAML test cases (`prompt` → expected task type + provider/model) and exits with a CI-friendly status code.

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
pip install -e ".[mcp]"
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

| Doc | What's in it |
|---|---|
| [Security guide](docs/security.md) | Threat model, what's exposed by default, every security control, secrets, recommended deployment posture. |
| [Deployment guide](docs/deployment.md) | Docker, compose, nginx/Caddy reverse proxies, Postgres setup, Kubernetes manifests, hardening checklist. |
| [OpenClaw integration](docs/integrations/openclaw.md) | Per-channel routing for the OpenClaw personal AI assistant. |

## What it doesn't do (yet)

- **No Anthropic provider yet** — planned, same shape as the other OpenAI-compatible ones.
- **The keyword classifier is hand-curated.** Rules + embedding prototypes are both YAML-editable, but they aren't a substitute for thinking about your domain.
- **The semantic cache is in-memory only.** Single-instance only until a Redis backend lands. Do not enable for legal / medical / financial / per-user PII workloads — see [docs/security.md](docs/security.md).
- **Cost preview uses a token estimate** (off by maybe 10-15% vs the real tokenizer). Good for routing decisions, not for billing. Actual response cost uses provider-reported tokens and is exact.
- **Streaming has no fallback mid-response.** If the first chunk lands and the connection drops, the stream aborts. Non-streaming requests fall back cleanly.
- **No per-IP rate limiting or multi-tenant auth.** The spend circuit-breaker (`MAX_DAILY_USD`) protects against runaway cost; per-IP / per-token rate limiting belongs in a reverse proxy.
- **Single-instance only.** Both the semantic cache and the persistent request log are local; running multiple replicas gives you fragmented state until both are externalised. Postgres is supported for the log today (`[postgres]` extra); Redis for the cache is on the roadmap.

## Roadmap

Short list, in priority order. Each item reinforces the core promise — *right model, right cost, right health, right channel* — without becoming a different product.

- **Anthropic provider** (same shape as the other OpenAI-compatible ones).
- **Per-request latency in `request_log`** so the A/B page can render p50/p95 columns.
- **Redis-backed shared semantic cache + provider health tracker** so multiple replicas share state.
- **Per-tenant cache scoping + auth** for multi-user / SaaS use.
- **Dashboard polish** — filtering on the Requests page, CSV export, budget-alert webhooks.
- **Exact tokenizers** (`tiktoken` etc.) as an optional `tokenizers` extra so cost preview is exact for billing.
- **Plugin system** so the community can add providers without forking.
- **TypeScript SDK** to lower integration friction.

## Explicitly out of scope

These adjacent features would turn Switchboard into a different product. We deliberately don't build them so the project stays sharp:

- **Fine-tuning management** — use OpenAI / Together / Replicate's own tooling.
- **Vector databases / RAG platform** — Pinecone, Weaviate, Chroma, pgvector exist for this.
- **Full agent framework / workflow builder** — LangChain, LlamaIndex, n8n are dedicated tools.
- **Enterprise SSO / SCIM / SOC2 dashboards** — these belong in a commercial wrapper, not the core router.
- **Multi-tenant SaaS hosting** — Switchboard is self-hosted by design. The community can build a hosted offering on top.
- **Prompt management / version control / playground-as-a-service** — Langfuse, Helicone, PromptLayer already do this well.

If your need is one of these, you're better served by the dedicated tools. Switchboard composes with them — sit it between your app and the provider, and use whichever observability/RAG/agent layer you prefer on top.

## Contributing

PRs welcome. Open an issue first for anything beyond a typo so we can talk through the approach.

```bash
pip install -e ".[dev,embeddings]"
pytest
ruff check . && black --check .
```

## License

MIT. See [LICENSE](LICENSE).
