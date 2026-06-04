# Routing recipes

Pre-baked routing strategies you can drop in. Copy or symlink one over `configs/config.yaml`:

```bash
cp configs/recipes/cost-aggressive.yaml configs/config.yaml
# or
ln -sf recipes/cost-aggressive.yaml configs/config.yaml
```

Then restart the server. None of these require any code changes — they're pure config.

| Recipe | Wedge | Needs |
|---|---|---|
| **cost-aggressive** | Cheapest route that gets the job done. Heavy use of local Ollama; small Groq for everything else. | `OLLAMA_BASE_URL` + `llama3.2:1b` pulled; `GROQ_API_KEY` |
| **quality-first** | Premium model per task; no penny-pinching. | `OPENAI_API_KEY` (preferred) or `GROQ_API_KEY` |
| **local-first** | Try local Ollama first; cloud only when local fails. Best for privacy / offline. | `OLLAMA_BASE_URL` |
| **balanced** | Cheap by default, automatic fallback to bigger models when the cheap one rate-limits. | `GROQ_API_KEY`, `GEMINI_API_KEY` |

If you're not sure which to start with, **balanced** is the safest default for production use.
