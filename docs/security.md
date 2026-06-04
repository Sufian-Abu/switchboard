# Security guide

This document describes Switchboard's security model, the boundaries it does and doesn't enforce, and the controls available to operators. Read this before exposing a Switchboard instance to anything beyond `localhost`.

## Threat model in one paragraph

Switchboard is built for small teams that run their own LLM routing in a controlled environment. It assumes the operator owns the infrastructure, the provider API keys, and the network boundary. It is **not** designed as a public multi-tenant SaaS. It does not have per-user identity, key rotation, or per-tenant isolation built in. If you need those, put Switchboard behind a reverse proxy that does, or wait for the multi-tenant work on the roadmap.

## What the defaults expose

When you start Switchboard with no security-related env vars set, the following endpoints are reachable by anyone who can reach the port:

| Path | What it exposes |
|---|---|
| `/v1/chat/completions` | Routes a request to a provider using your API keys |
| `/v1/chat/compare` | Runs a prompt against every priced model in parallel |
| `/v1/chat/estimate` | Token estimate + cost preview (no upstream calls) |
| `/v1/chat/route` | Routing decision only (no upstream calls) |
| `/v1/cache/stats` | Semantic cache hits/misses/entries |
| `/dashboard` and subpaths | All recorded request history, prompts, models, USD spend |
| `/docs` | Swagger UI listing every endpoint |

Default deployment is appropriate for: localhost development, an internal-only IP that isn't routable from the public internet, or a private VPC where every caller is trusted.

Default deployment is NOT appropriate for: public IPs, ngrok-style tunnels for demos, anywhere a screenshot of the URL bar reveals the host to untrusted parties.

## Controls

### 1. Bearer token on `/v1/*`

```env
API_TOKEN=sk-switchboard-<long-random>
```

When set, every request to `/v1/*` must include `Authorization: Bearer <API_TOKEN>` or it gets a 401. Tokens are checked in plaintext (no hashing) and there is no rotation API — if a token is compromised, change the env var and restart.

### 2. Dashboard auth

```env
API_TOKEN=sk-switchboard-<long-random>
DASHBOARD_AUTH=true
```

When both are set, `/dashboard/*` and `/v1/cache/stats` also require auth. Browsers can authenticate via HTTP Basic auth — the password field is the `API_TOKEN`, the username is ignored — so the native browser credential prompt works without any extra setup. Programmatic clients can use Bearer as usual.

If you don't trust HTTP Basic auth on its own (it is) — front Switchboard with a reverse proxy that adds TLS and any additional access controls you need.

### 3. Spend circuit-breaker

```env
MAX_DAILY_USD=10.0
```

When the day's recorded `estimated_usd` in `request_log` crosses this cap, all subsequent `/v1/chat/{completions,compare}` requests return a 503 until the next UTC day. Protects against runaway-cost incidents (loops, leaked tokens, malicious clients). The cap is based on Switchboard's cost *estimate*, not the actual provider invoice — keep the cap a bit below your real comfort level.

### 4. Reject client-supplied `model:`

```env
ALLOW_CLIENT_MODEL_OVERRIDE=false  # this is the default
```

By default, a request with `"model": "gpt-4o"` is rejected with 400 — the routing policy decides the model, not the client. Set this to `true` only if you want the legacy behaviour, which routes client-overridden requests through the mock provider (safe but confusing).

### 5. Semantic cache scoping

The semantic cache is opt-in per rule (`cache: true` in `configs/config.yaml`) and opt-in globally (`ENABLE_SEMANTIC_CACHE=true`). It is **not safe** for:

- Legal, financial, or medical workloads
- Anything involving PII or per-customer data
- Workloads where prompt negation, named entities, or numbers materially change the right answer

Per-tenant cache scoping is on the roadmap; until it exists, treat the cache as a tool for FAQ / public-data / generic-summary workloads only.

## Secrets management

API keys (`OPENAI_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `API_TOKEN`) come from `apps/server/.env` or the process environment.

- `apps/server/.env` is gitignored.
- The Docker image's `.dockerignore` excludes `.env` from the build context.
- In Docker, pass secrets via `-e` flags, `--env-file`, or the orchestrator's secret store (Kubernetes Secret, Docker Swarm secret, etc.). Don't bake them into the image.
- The persistent log `data/router.db` contains every prompt and response. Treat it as sensitive. Back it up with the same controls you'd use for application logs.

## What gets logged

Every request that reaches a provider (or fails) writes a row to `request_log` with:

- Timestamp, request ID, endpoint
- Selected provider, selected model, attempts chain
- Prompt token count, completion token count, USD estimate
- Success/failure status, error message

Prompts themselves are **not** logged today. Response content is **not** logged today. If you need either for audit, you'll have to extend the schema and write the log entries yourself — and accept that this materially changes the privacy model.

## What is NOT protected

- **Rate limiting.** A valid token can fire as many requests per second as the network allows. Front with `nginx limit_req`, Caddy `rate_limit`, or a similar tool if this matters.
- **Per-user identity.** All requests with a valid `API_TOKEN` are indistinguishable to Switchboard.
- **Key rotation.** Changing `API_TOKEN` requires a restart.
- **Audit trail of who.** We log the request, not the principal. Wrap with an authenticating proxy if you need this.
- **Multi-tenant isolation.** A single Switchboard instance is one tenant. Run multiple instances for multiple tenants, or wait for the roadmap item.

## Recommended deployment posture

1. Set `API_TOKEN` to a long random secret.
2. Set `DASHBOARD_AUTH=true`.
3. Set a `MAX_DAILY_USD` cap below your real budget ceiling.
4. Keep `ALLOW_CLIENT_MODEL_OVERRIDE=false` (the default).
5. Run behind a reverse proxy that terminates TLS, adds `Strict-Transport-Security`, and rate-limits inbound requests.
6. Mount `data/` as a volume that is backed up.
7. Restrict outbound egress from the Switchboard container to just the provider hostnames you actually use (especially valuable when the spend cap could otherwise be triggered by accidentally calling the wrong provider).

See [deployment.md](deployment.md) for concrete reverse-proxy snippets.

## Reporting a security issue

Please don't open public GitHub issues for security problems. Email the maintainer or open a private Security Advisory on the repo.
