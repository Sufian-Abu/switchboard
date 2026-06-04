# Switchboard

> Drop-in OpenAI-compatible LLM router that picks the right model per request, falls back when one is down, and shows you the cost — before, during, and after the call.

---

## Why use this

Most "LLM router" projects are passthrough proxies. Switchboard goes further: it **classifies each request**, picks a provider/model based on **YAML rules and live pricing**, shows you the **expected cost across every model before you send**, and ships with a **dashboard** so you actually know what your spend looks like.

| | **Switchboard** | LiteLLM | RouteLLM | OpenRouter |
|---|---|---|---|---|
| OpenAI-compatible endpoint | ✓ | ✓ | partial | ✓ |
| Task-aware routing (rewrite vs reasoning vs ...) | ✓ | partial | ✓ research | ✗ |
| YAML rules for routing | ✓ | partial | ✗ | ✗ |
| **Pre-flight cost preview per model** | ✓ | ✗ | ✗ | ✗ |
| **Per-response cost block** | ✓ | ✗ | ✗ | partial |
| **Cost-aware routing (`prefer: cheapest`)** | ✓ | ✗ | ✗ | ✗ |
| **Built-in dashboard** | ✓ | ✗ | ✗ | hosted only |
| Fallback chain on errors | ✓ | ✓ | ✗ | ✓ |
| Per-rule semantic cache | ✓ | ✗ | ✗ | ✗ |
| Hybrid keyword + embeddings classifier | ✓ | ✗ | ✓ | ✗ |
| Streaming with routing metadata | ✓ | partial | ✗ | ✓ |
| Eval harness (CI-friendly) | ✓ | ✗ | partial | ✗ |
| Local Ollama first-class | ✓ | ✓ | ✗ | ✗ |
| Self-host | ✓ | ✓ | ✓ | ✗ |

---

## Quick start

### Docker (recommended)

```bash
docker run -p 8000:8000 \
  -e GROQ_API_KEY=$GROQ_API_KEY \
  -e GEMINI_API_KEY=$GEMINI_API_KEY \
  ghcr.io/<your-username>/switchboard:latest
```

Open http://localhost:8000 — you land on the dashboard.

### docker-compose (with local Ollama)

```bash
git clone https://github.com/<your-username>/switchboard
cd switchboard
cp apps/server/.env.example apps/server/.env  # add your keys
docker compose --profile with-ollama up
```

This launches the router on `:8000` and Ollama on `:11434`. The first request to a routing rule that uses Ollama will pull the model on demand.

### Python (manual install)

```bash
git clone https://github.com/<your-username>/switchboard
cd switchboard
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # add ",embeddings" for the hybrid classifier
cp apps/server/.env.example apps/server/.env  # add your keys

cd apps/server
PYTHONPATH=../../packages:./ uvicorn app.main:app --reload
```

Open http://localhost:8000.

---

## What you get out of the box

### Built-in dashboard

```
http://localhost:8000/dashboard
```

- **Overview** — KPIs (requests, success rate, total USD, tokens), cost-by-provider bar, task-type doughnut, recent calls.
- **Playground** — type a prompt, see live cost preview across every priced model as you type, run it (streaming), see the actual routing decision + cost.
- **Requests** — full audit log: timestamp, request ID, provider, model, tokens, USD, status, attempt count.
- **Cost** — last-7-day spend, daily line chart, by-provider and by-model tables.

### One curl gets you everything

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"please rewrite this email politely"}]}'
```

Response shape (truncated):

```jsonc
{
  "id": "chatcmpl-…",
  "object": "chat.completion",
  "choices": [{ "message": { "role": "assistant", "content": "…" }, ... }],
  "usage": { "prompt_tokens": 43, "completion_tokens": 13, "total_tokens": 56 },

  // Why this provider was chosen + the full attempt chain
  "routing": {
    "task_type": "rewrite",
    "selected_provider": "groq",
    "selected_model": "llama-3.1-8b-instant",
    "reason": "Matched routing rule: rewrite_to_groq_small",
    "attempts": [
      { "provider": "groq", "model": "llama-3.1-8b-instant", "status": "succeeded", "upstream_status": null, "error": null }
    ]
  },

  // Actual USD cost computed from token counts × per-model rates
  "cost": {
    "currency": "USD",
    "estimated_usd": 3.19e-06,
    "input_usd": 2.15e-06,
    "output_usd": 1.04e-06,
    "input_rate_per_million": 0.05,
    "output_rate_per_million": 0.08,
    "pricing_known": true
  }
}
```

### Pre-flight cost preview

Before you send, ask what each model would cost:

```bash
curl -X POST http://localhost:8000/v1/chat/estimate \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"your prompt"}],"assumed_max_tokens":256}'
```

Returns a USD `min` (input only) and `max` (input + assumed max output) per priced model, plus the cheapest/most-expensive picks.

### Streaming

Add `"stream": true` to the chat request. Response is `text/event-stream` with OpenAI-shaped delta events, then a custom router-metadata event with routing + cost + usage, then `data: [DONE]`. OpenAI clients ignore the metadata field — backwards-compatible.

---

## Configuration

### Routing rules — `configs/config.yaml`

```yaml
default:
  provider: mock
  model: mock-default-model

routing:
  rules:
    # Simple text edits → small Groq model
    - name: rewrite_to_groq_small
      when: { task_type: rewrite }
      use:  { provider: groq, model: llama-3.1-8b-instant }

    # Reasoning → cheapest of [Groq large, local Ollama]
    - name: reasoning_cheapest
      when: { task_type: reasoning }
      use:       { provider: groq, model: llama-3.3-70b-versatile }
      fallbacks:
        - { provider: ollama, model: llama3.2:1b }
      prefer: cheapest        # reorder candidates by expected USD
      max_cost_per_call: 0.01 # filter — skip rule entirely if all candidates exceed
      cache: true             # opt-in to the semantic cache for this rule
```

Reload by restarting the server (cached at startup for speed).

### Pricing — `configs/pricing.yaml`

```yaml
groq:
  llama-3.1-8b-instant:   { input: 0.05, output: 0.08 }
  llama-3.3-70b-versatile: { input: 0.59, output: 0.79 }
gemini:
  gemini-2.5-flash:        { input: 0.30, output: 2.50 }
ollama:
  "*":                     { input: 0.0,  output: 0.0 }   # wildcard — all local models = $0
```

All values are USD per 1,000,000 tokens. Models without a price get `pricing_known: false` in the response and are sorted last under `prefer: cheapest`.

### Environment — `apps/server/.env`

```env
APP_ENV=development
API_TOKEN=                            # empty = auth off; set to require bearer on /v1/*

OPENAI_API_KEY=
GROQ_API_KEY=
GEMINI_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434

# Phase 2b opt-ins (require `.[embeddings]` extra)
ENABLE_EMBEDDING_CLASSIFIER=false
ENABLE_SEMANTIC_CACHE=false
SEMANTIC_CACHE_SIMILARITY_THRESHOLD=0.95
```

---

## Architecture

```
┌──────────────┐
│ HTTP request │
└──────┬───────┘
       ▼
┌─────────────────────┐
│ BearerAuth          │  optional, on /v1/*
├─────────────────────┤
│ RequestIDMiddleware │  inbound rid honored; otherwise generated
├─────────────────────┤
│ Route               │  /v1/chat/completions, /v1/chat/estimate, ...
└──────┬──────────────┘
       ▼
┌──────────────────────────────────────────────────────────┐
│ ChatService.create_completion / create_completion_stream │
│                                                          │
│   1.  TaskClassifier.classify()                          │
│       └─ keyword rules → embeddings fallback             │
│                                                          │
│   2.  DecisionEngine.decide()                            │
│       └─ match rule → preference reorder → candidate chain│
│                                                          │
│   3.  Semantic cache lookup (if rule opts in)            │
│                                                          │
│   4.  for (provider, model) in chain:                    │
│         provider.chat()   or   provider.stream()         │
│         on retryable error → next candidate              │
│                                                          │
│   5.  CostEngine.estimate()                              │
│   6.  Persist request log row                            │
│   7.  Return ChatCompletionResponse (+ routing + cost)   │
└──────────────────────────────────────────────────────────┘
```

**Project layout:**

```
switchboard/
├── apps/server/                # FastAPI app
│   ├── app/
│   │   ├── api/                #   routes + deps
│   │   ├── core/               #   settings + config loader
│   │   ├── db/                 #   SQLAlchemy models + async session
│   │   ├── schemas/            #   Pydantic models
│   │   ├── services/           #   ChatService, dashboard_service, estimate_service
│   │   ├── templates/          #   Jinja2 (dashboard + playground)
│   │   └── main.py             #   FastAPI app, lifespan, middleware
│   └── .env.example
├── packages/router/            # Provider-agnostic routing engine
│   ├── classifier.py           #   hybrid keyword + embeddings classifier
│   ├── decision_engine.py      #   YAML rules → RoutingDecision
│   ├── preference.py           #   cost-aware candidate reorder
│   ├── costing.py              #   USD estimation
│   ├── embedder.py             #   optional sentence-transformers wrapper
│   ├── cache.py                #   in-memory semantic cache
│   ├── eval.py                 #   `python -m router.eval` CLI
│   ├── errors.py               #   typed exceptions
│   └── providers/              #   mock, openai, groq, gemini, ollama
├── configs/
│   ├── config.yaml             # routing rules
│   └── pricing.yaml            # per-model USD/1M-token rates
├── evals/
│   └── example.yaml            # sample test suite
├── tests/                      # 84 tests, all offline
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

---

## Endpoints

| Method · path | Purpose | Auth required* |
|---|---|---|
| POST `/v1/chat/completions` | OpenAI-compatible chat (streaming or JSON) | ✓ |
| POST `/v1/chat/estimate` | Pre-flight cost preview across all priced models | ✓ |
| GET `/v1/cache/stats` | Semantic cache stats | ✓ |
| GET `/health` | Liveness probe | — |
| GET `/` | Redirect to `/dashboard` | — |
| GET `/dashboard` | Overview KPIs + breakdowns | — |
| GET `/dashboard/playground` | Interactive prompt + live cost + streaming chat | — |
| GET `/dashboard/requests` | 100 most-recent calls audit | — |
| GET `/dashboard/cost` | Last-7-day cost analytics | — |
| GET `/docs` | Swagger UI | — |

`*` only when `API_TOKEN` is set in the environment. Dashboard pages stay open for local use; put them behind a reverse proxy for production.

---

## Testing

The pytest suite is fully offline — `TestClient` + MockProvider, no API keys required.

```bash
pytest                       # all 84 tests, ~1s
pytest tests/test_chat_endpoint.py -v
```

### Eval harness

Score routing decisions against a YAML test suite:

```bash
python -m router.eval --cases evals/example.yaml
```

Sample output:

```
[PASS] simple_rewrite                  -> groq/llama-3.1-8b-instant
[PASS] summary_request                 -> gemini/gemini-2.5-flash
[PASS] extraction_request              -> groq/llama-3.3-70b-versatile
[PASS] reasoning_uses_local_when_cheapest -> ollama/llama3.2:1b
[PASS] generic_chat_falls_through      -> ollama/llama3.2:1b

5/5 passed; 0 failed
```

Exit code 0/1 — wire into CI.

---

## Provider matrix

| Provider | Chat | Streaming | Notes |
|---|---|---|---|
| Mock | ✓ | ✓ | Zero-config default; word-by-word streaming for tests |
| Groq | ✓ | ✓ | OpenAI-compatible; needs `GROQ_API_KEY` |
| Gemini | ✓ | ✓ | OpenAI-compat endpoint; needs `GEMINI_API_KEY` |
| OpenAI | ✓ | ✓ | Needs `OPENAI_API_KEY` |
| Ollama | ✓ | ✓ | Local; needs daemon on `OLLAMA_BASE_URL` |

Adding a provider is ~50 lines: implement `chat()` + `stream()` on `BaseProvider`, register in `apps/server/app/api/deps.py:get_provider`.

---

## Roadmap

**Phase 2c — multi-user**
- Rate limiting (per-token, per-IP)
- Plugin system for custom providers
- Multi-tenant configs

**Phase 3 — ecosystem**
- More providers: Anthropic, AWS Bedrock, Together, Replicate, Cohere
- Vector store backend for semantic cache (sqlite-vss / chromadb / qdrant)
- Realtime dashboard via SSE push
- Official Python + TypeScript SDKs

---

## Contributing

Bug reports, feature requests, and PRs welcome. Please open an issue before starting a large PR.

```bash
pip install -e ".[dev]"
pytest                            # before submitting
ruff check . && black --check .   # formatting
```

---

## License

MIT — see [LICENSE](LICENSE).
