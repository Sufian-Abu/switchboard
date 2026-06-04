# Deployment guide

Practical patterns for running Switchboard somewhere other than your laptop. Read [security.md](security.md) first — most of the choices here exist to give the security controls something to attach to.

## Quickstart: local Docker

```bash
docker run -p 8000:8000 \
  -e GROQ_API_KEY=$GROQ_API_KEY \
  -e GEMINI_API_KEY=$GEMINI_API_KEY \
  -v $(pwd)/configs:/app/configs:ro \
  -v switchboard-data:/app/data \
  ghcr.io/sufian-abu/switchboard:latest
```

Fine for `localhost`. Not fine for anything else.

## docker-compose: router + Ollama + reverse proxy

This is the recommended posture for a single-node deployment that's reachable from outside the host. It adds Caddy in front so you get TLS termination, rate limiting, and a deny-by-default network policy.

```yaml
services:
  switchboard:
    image: ghcr.io/sufian-abu/switchboard:latest
    expose:
      - "8000"
    env_file:
      - apps/server/.env   # API_TOKEN, DASHBOARD_AUTH=true, MAX_DAILY_USD, provider keys
    environment:
      OLLAMA_BASE_URL: http://ollama:11434
    volumes:
      - ./configs:/app/configs:ro
      - switchboard-data:/app/data
    depends_on:
      ollama:
        condition: service_healthy
        required: false

  ollama:
    image: ollama/ollama:latest
    profiles: ["with-ollama"]
    volumes:
      - ollama-models:/root/.ollama
    healthcheck:
      test: ["CMD", "ollama", "list"]
      interval: 10s
      timeout: 3s
      retries: 5

  caddy:
    image: caddy:2
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config
    depends_on:
      - switchboard

volumes:
  switchboard-data:
  ollama-models:
  caddy-data:
  caddy-config:
```

`Caddyfile`:

```caddy
switchboard.example.com {
    # Switchboard already does its own bearer auth; Caddy just terminates TLS,
    # rate-limits, and forwards.
    rate_limit {
        zone shared {
            key {remote_host}
            events 60
            window 60s
        }
    }
    reverse_proxy switchboard:8000
    encode gzip
    log {
        output stdout
        format console
    }
}
```

Bring it up with:

```bash
docker compose --profile with-ollama up -d
```

Caddy handles ACME, TLS renewal, HTTP→HTTPS redirect, and `Strict-Transport-Security` automatically. You get `https://switchboard.example.com` with sane defaults and rate limiting.

## Behind nginx (if you already have one)

```nginx
upstream switchboard {
    server 127.0.0.1:8000;
}

server {
    listen 443 ssl http2;
    server_name switchboard.example.com;

    ssl_certificate     /etc/letsencrypt/live/switchboard.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/switchboard.example.com/privkey.pem;

    # 60 requests per minute per client IP. Tune to taste.
    limit_req_zone $binary_remote_addr zone=switchboard:10m rate=1r/s;
    limit_req zone=switchboard burst=20 nodelay;

    # Streaming endpoints benefit from disabling proxy buffering.
    location /v1/chat/completions {
        proxy_pass http://switchboard;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        proxy_pass http://switchboard;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## Postgres (instead of SQLite)

SQLite is the default and is fine for single-instance use. For shared deployments or longer retention, switch to Postgres. The schema is created on first boot by SQLAlchemy.

```bash
pip install -e ".[postgres]"

# In apps/server/.env:
DATABASE_URL=postgresql+asyncpg://switchboard:password@db.internal:5432/switchboard
```

Then in `docker-compose.yml`:

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_DB: switchboard
      POSTGRES_USER: switchboard
      POSTGRES_PASSWORD_FILE: /run/secrets/db_password
    volumes:
      - pg-data:/var/lib/postgresql/data
    secrets:
      - db_password

  switchboard:
    environment:
      DATABASE_URL: postgresql+asyncpg://switchboard:${DB_PASSWORD}@db:5432/switchboard
    depends_on:
      - db

volumes:
  pg-data:

secrets:
  db_password:
    file: ./secrets/db_password.txt
```

Day-grouping in the dashboard uses `CAST(ts AS DATE)`, which is portable across SQLite and Postgres without changes.

The in-memory semantic cache still doesn't share across replicas — Postgres helps with the request log + spend cap, but you'll need a Redis-backed cache for horizontal scaling. That's on the roadmap.

## Kubernetes

A minimal `Deployment` + `Service` + `Ingress`. The `Secret` carries the bearer token and provider keys.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: switchboard-secrets
type: Opaque
stringData:
  API_TOKEN: "sk-switchboard-<long-random>"
  GROQ_API_KEY: "gsk_..."
  GEMINI_API_KEY: "..."
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: switchboard
spec:
  replicas: 1   # The semantic cache and request log are local — running >1 replica needs work first.
  selector:
    matchLabels: { app: switchboard }
  template:
    metadata:
      labels: { app: switchboard }
    spec:
      containers:
        - name: switchboard
          image: ghcr.io/sufian-abu/switchboard:latest
          ports:
            - containerPort: 8000
          env:
            - name: DASHBOARD_AUTH
              value: "true"
            - name: ALLOW_CLIENT_MODEL_OVERRIDE
              value: "false"
            - name: MAX_DAILY_USD
              value: "50.0"
          envFrom:
            - secretRef:
                name: switchboard-secrets
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          volumeMounts:
            - mountPath: /app/data
              name: data
            - mountPath: /app/configs
              name: configs
              readOnly: true
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: switchboard-data
        - name: configs
          configMap:
            name: switchboard-configs
---
apiVersion: v1
kind: Service
metadata:
  name: switchboard
spec:
  selector: { app: switchboard }
  ports:
    - port: 80
      targetPort: 8000
```

Constraints to know:

- **`replicas: 1`.** The semantic cache is in-process. The persistent request log is local SQLite. Until both are externalised, scaling out horizontally will give you fragmented state. Vertical scaling works fine.
- **PVC for `/app/data`.** Otherwise the request log dies with the pod.
- **ConfigMap for `/app/configs`.** Lets you edit routing rules without rebuilding the image.

## Hardening checklist before going live

- [ ] `API_TOKEN` is set and is long (32+ random chars).
- [ ] `DASHBOARD_AUTH=true`.
- [ ] `MAX_DAILY_USD` is set below your real budget ceiling.
- [ ] `ALLOW_CLIENT_MODEL_OVERRIDE=false` (the default).
- [ ] Reverse proxy terminates TLS and rate-limits.
- [ ] `data/` is on persistent storage and backed up.
- [ ] Container runs as non-root (the Dockerfile already does this).
- [ ] Provider API keys come from a secret store, not committed env files.
- [ ] You've read [security.md](security.md) and accept the documented limitations.

## Monitoring

Switchboard's structured logs go to stdout. Pipe them into whatever you already use:

```
[2026-06-04 14:12:31] INFO llm-router.chat - [507e0ff91f2c] routed task_type=rewrite provider=groq model=llama-3.1-8b-instant
```

Each line tagged with the 12-char request ID. The request log table (`data/router.db`) is the source of truth for cost. Run nightly backups; consider streaming it to your warehouse if you want longer retention.

The `/health` endpoint returns 200 when the app is up. The Dockerfile already includes a healthcheck that polls it every 30s.

## Updating

```bash
docker pull ghcr.io/sufian-abu/switchboard:latest
docker compose up -d
```

The data volume survives. Routing/pricing config you mounted via volume survives. The in-memory cache is lost on restart by design.

## When to NOT use Switchboard

- **You need multi-tenant isolation today.** Wait for the roadmap or use OpenRouter.
- **You need horizontal scaling out of the box.** Single-instance only until the cache + request log are externalised.
- **You're routing legal/medical/financial workloads through the semantic cache.** Don't enable the cache. See [security.md](security.md).
- **You need an SLA on uptime.** Switchboard is a self-operated tool; the SLA is the one you give yourself.
