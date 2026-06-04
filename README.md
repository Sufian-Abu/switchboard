# Switchboard

A cost-aware LLM router with a built-in dashboard. Drop it in front of your app, send OpenAI-shaped requests, and stop worrying about which provider to use, what it costs, or what to do when one of them goes down.

## The problem

Most apps using LLMs today aren't sticking to one provider. You probably have an OpenAI key, an Anthropic or Gemini key, maybe Groq for cheap fast inference, and Ollama for the things you'd rather keep on-device. Every team I've talked to ends up in roughly the same place: a thin Python file that picks a provider based on some half-written rules, a spreadsheet somewhere with last month's spend, and a quiet feeling that nobody actually knows where the money is going.

The reason isn't that the problem is hard. The reason is that the tools for it pull you in two directions. On one side you have proxies like LiteLLM and OpenRouter — they unify the API surface, which is genuinely useful, but they don't really *route*. They send your request to whatever model you named, hand back the response, and that's the end of their job. They have no opinion about whether a one-line rewrite request should hit GPT-4 or a 1B local model. On the other side you have research projects like RouteLLM that take routing seriously, but you don't really run those in production.

What's missing is a thing that sits in the middle. Something that:

- Looks at the request and decides what kind of task it is (rewrite, summarisation, structured extraction, reasoning, plain chat).
- Picks a provider/model from a YAML file you actually control, with fallback when one provider rate-limits you.
- Shows you, in real time, how much each call costs — and what each candidate model *would have* cost.
- Gives you a dashboard so you can answer "what did we spend yesterday" without writing a script.

That's what this is.

## What it does

A request comes in on `POST /v1/chat/completions`. The classifier looks at the messages, decides this is (say) a `summarization` task. The decision engine consults `configs/config.yaml`, finds the rule for that task type, and reads off a provider + model — plus an ordered list of fallbacks if the primary fails. If the rule says `prefer: cheapest`, the candidates get reordered by expected USD cost before picking the primary. If everything succeeds the response comes back in OpenAI's normal shape, with two extra blocks: `routing` (what was chosen and why) and `cost` (actual USD spent on this call). A row goes into a local SQLite database. The dashboard reads from that database.

That's the whole thing. The interesting bits are in the details — how the fallback chain interacts with streaming, how the cost preview works without burning tokens, how the semantic cache hooks into rules without breaking anything else — but the shape is small enough to fit in a paragraph.

## See it in action

> Screenshots live in `docs/screenshots/`. If you've cloned the repo and haven't added them yet, follow the instructions in [docs/screenshots/README.md](docs/screenshots/README.md) to capture them in 30 seconds.

| | |
|---|---|
| **Overview** | KPIs, cost-by-provider chart, recent calls, task-type breakdown |
| **Playground** | Live cost preview per model as you type, then run with streaming |
| **Requests** | Full audit log: request ID, provider, model, tokens, USD, attempts |
| **Cost** | 7-day spend, daily chart, by-provider and by-model tables |

![Dashboard overview](docs/screenshots/dashboard.png)

![Playground with live cost preview](docs/screenshots/playground.png)

![Cost analytics](docs/screenshots/cost.png)

## Quick start

### With Docker (one command)

```bash
docker run -p 8000:8000 \
  -e GROQ_API_KEY=$GROQ_API_KEY \
  -e GEMINI_API_KEY=$GEMINI_API_KEY \
  ghcr.io/<your-username>/switchboard:latest
```

Open http://localhost:8000 and you land on the dashboard. Send a request through `/v1/chat/completions` and watch it appear there.

### With docker-compose (router + local Ollama)

```bash
git clone https://github.com/<your-username>/switchboard
cd switchboard
cp apps/server/.env.example apps/server/.env   # then fill in your keys
docker compose --profile with-ollama up
```

This runs the router on `:8000` and a local Ollama daemon on `:11434`. Any routing rule that uses Ollama will work straight away — pull the model the first time and it stays cached.

### From source

```bash
git clone https://github.com/<your-username>/switchboard
cd switchboard
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Optional: install the embeddings extra for the hybrid classifier and the
# semantic cache. Adds ~600 MB of torch. Most people don't need it day-one.
pip install -e ".[dev,embeddings]"

cp apps/server/.env.example apps/server/.env    # then fill in your keys
cd apps/server
PYTHONPATH=../../packages:./ uvicorn app.main:app --reload
```

## How a request actually flows

You define rules in `configs/config.yaml`. Here's a small slice:

```yaml
routing:
  rules:
    # Cheap, fast text edits go to Groq's smallest model.
    - name: rewrite_to_groq_small
      when: { task_type: rewrite }
      use:  { provider: groq, model: llama-3.1-8b-instant }

    # Reasoning is more expensive on cloud, so try local Ollama first if
    # it's cheaper, and fall back to Groq's 70B model if Ollama is down.
    - name: reasoning_cheapest
      when: { task_type: reasoning }
      use:       { provider: groq, model: llama-3.3-70b-versatile }
      fallbacks:
        - { provider: ollama, model: llama3.2:1b }
      prefer: cheapest
      max_cost_per_call: 0.01
      cache: true
```

Now hit the endpoint:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"please rewrite this email politely"}]}'
```

You get back a normal OpenAI-shaped response with two extra blocks bolted on the side:

```jsonc
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "llama-3.1-8b-instant",
  "choices": [{ "message": { "role": "assistant", "content": "..." }, ... }],
  "usage": { "prompt_tokens": 43, "completion_tokens": 13, "total_tokens": 56 },

  "routing": {
    "task_type": "rewrite",
    "selected_provider": "groq",
    "selected_model": "llama-3.1-8b-instant",
    "reason": "Matched routing rule: rewrite_to_groq_small",
    "attempts": [
      { "provider": "groq", "model": "llama-3.1-8b-instant",
        "status": "succeeded", "upstream_status": null, "error": null }
    ]
  },

  "cost": {
    "currency": "USD",
    "estimated_usd": 0.00000319,
    "input_usd": 0.00000215,
    "output_usd": 0.00000104,
    "input_rate_per_million": 0.05,
    "output_rate_per_million": 0.08,
    "pricing_known": true
  }
}
```

If you want to know what something *would* cost before you send it, hit `/v1/chat/estimate` with the same body. You get back a USD min/max per priced model, plus the cheapest and most expensive picks. The playground page does this for you as you type.

If you want streaming, add `"stream": true`. You get standard OpenAI SSE chunks, then a `data: {"x_smart_router_meta": true, "routing": {...}, "cost": {...}}` event with the routing + cost numbers, then `data: [DONE]`. OpenAI clients ignore the metadata event so existing SDKs keep working unchanged.

## What's in the box

**Providers.** Mock (zero-config default for tests), Groq, Gemini, OpenAI, and Ollama. The three OpenAI-compatible cloud providers share a streaming helper at `packages/router/providers/_openai_compat.py`, so adding another one (Together, Fireworks, anything OpenAI-shaped) is ~30 lines. Ollama is implemented separately because its streaming format is JSONL rather than SSE.

**Cost engine.** Pricing lives in `configs/pricing.yaml` — change it without touching code. Every successful call gets a USD breakdown. The cost preview endpoint uses a rule-of-thumb token estimator (max of chars/4 and words×1.3) so you don't burn tokens just to know what something would cost. It's not exact, but it's the right order of magnitude.

**Classifier.** Two layers, used in order. The keyword classifier is a hand-curated set of triggers per task type — fast, deterministic, no model loading. If nothing matches, the optional embeddings classifier kicks in: it embeds your prompt and the prototype examples for each task type, picks the closest. That second layer needs the `embeddings` extra (`pip install ".[embeddings]"`); the first works on its own.

**Fallback chain.** Each rule can list fallback (provider, model) pairs after `use:`. If the primary fails with a retryable error (rate limit, 5xx, network), the next candidate is tried. 4xx errors are not retried — a malformed request won't succeed elsewhere either. Streaming doesn't fall back mid-response (that would corrupt output) — if the first chunk hasn't been sent yet you still get a fallback try, but once a single chunk lands, errors abort the stream.

**Semantic cache.** Per-rule opt-in (`cache: true`). When enabled, the cache embeds the prompt, looks for a similar one above a cosine-similarity threshold for the same model. Hit → return the cached response, no provider call. Miss → call the provider, store the response. In-memory, LRU eviction at 256 entries by default. Good enough for a single instance; replace with sqlite-vss or a real vector store before scaling horizontally.

**Dashboard.** Server-rendered Jinja templates, Tailwind via CDN, Chart.js for charts, HTMX for any future interactivity. No build step, no React app to maintain.

**Auth.** Disabled by default. Set `API_TOKEN` in the environment and `/v1/*` paths require `Authorization: Bearer <token>`. The dashboard stays open — put it behind a reverse proxy if you need to lock it down.

## Configuration

Three files do all the work. They're meant to be edited.

### `configs/config.yaml` — routing rules

Every rule has a `when:` (task type to match), a `use:` (primary provider+model), and optional `fallbacks:`. The cost-aware fields (`prefer:`, `max_cost_per_call:`) and the `cache:` flag are all optional.

### `configs/pricing.yaml` — per-model pricing

USD per million tokens, split by input/output. Use `"*"` as a model key for a wildcard ("all of this provider's models cost $0", which is true for Ollama). Models without entries get `pricing_known: false` and are sorted last under `prefer: cheapest`.

### `apps/server/.env` — secrets and feature flags

| Variable | Default | What it does |
|---|---|---|
| `APP_NAME` | `Switchboard` | Used in `/health` and the dashboard header |
| `API_TOKEN` | _(empty)_ | Set to require bearer-token auth on `/v1/*` |
| `OPENAI_API_KEY` | _(empty)_ | Needed only when a rule routes to OpenAI |
| `GROQ_API_KEY` | _(empty)_ | Needed only when a rule routes to Groq |
| `GEMINI_API_KEY` | _(empty)_ | Needed only when a rule routes to Gemini |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Where to find the Ollama daemon |
| `ENABLE_EMBEDDING_CLASSIFIER` | `false` | Use embeddings as a classifier fallback |
| `ENABLE_SEMANTIC_CACHE` | `false` | Turn on the semantic cache (rules also need `cache: true`) |

## API surface

| Method · path | Purpose |
|---|---|
| `POST /v1/chat/completions` | OpenAI-compatible chat (JSON or SSE) |
| `POST /v1/chat/estimate` | Cost preview across every priced model |
| `GET  /v1/cache/stats` | Semantic cache hits/misses/entries |
| `GET  /health` | Liveness probe |
| `GET  /` | Redirect to the dashboard |
| `GET  /dashboard{,/playground,/requests,/cost}` | Web UI |
| `GET  /docs` | Auto-generated Swagger UI |

## Testing

The pytest suite runs fully offline. `TestClient` plus the `MockProvider` cover every code path; no API keys required.

```bash
pytest                       # ~1 second, 84 tests
pytest -v tests/test_chat_endpoint.py
```

There's also an eval harness for routing quality. Cases live in YAML; assertions are simple ("this prompt should route to provider X", "this prompt should hit model whose name contains Y"). Run it with:

```bash
python -m router.eval --cases evals/example.yaml
```

You get a per-case PASS/FAIL report and an exit code, so it slots into CI without further glue.

## What it doesn't do (yet)

- **No Anthropic provider.** Planned for the next phase. Easy to add — same shape as the OpenAI-compatible ones, just a different request body.
- **The keyword classifier is hand-curated.** It works well for the common task types but you'll need to add rules as your usage drifts. The embeddings classifier helps but isn't a replacement for thinking about your domain.
- **The semantic cache is in-memory only.** Fine for a single instance, not for multi-instance deployments.
- **Cost preview uses a rule-of-thumb token estimator.** Off by maybe 10-15% versus the real tokenizer. Good for decisions, not for billing.
- **Streaming has no fallback mid-response.** If the primary breaks after sending the first chunk, the stream aborts. (The non-streaming path falls back cleanly.)
- **No rate limiting or multi-tenant auth.** A single bearer token is all that's built in. Put it behind nginx/Caddy if you need more.

I'd rather list these honestly than discover them on launch day.

## Roadmap

The short version: more providers, a proper vector store backend for the cache, rate limiting, a small plugin system so the community can add providers without forking, and at some point a TypeScript SDK. None of these are blockers for what's here today.

## Contributing

PRs welcome. Open an issue first for anything larger than a typo so we can talk through the approach.

```bash
pip install -e ".[dev,embeddings]"
pytest
ruff check . && black --check .
```

## License

MIT. See [LICENSE](LICENSE).
