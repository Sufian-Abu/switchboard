# Screenshots

The main README references three images in this folder. Capture them once, commit, done.

## 1. Generate some sample traffic first

You need data in the dashboard before screenshots are interesting. Run the server, then send a handful of requests so charts and tables aren't empty.

```bash
cd apps/server
PYTHONPATH=../../packages:./ uvicorn app.main:app --reload

# In another shell, fire 15-20 mixed requests
for prompt in \
  "please rewrite this email politely" \
  "give me a short summary of: Python was created in 1991." \
  "return JSON with extract fields from: John Doe john@x.com" \
  "discuss the architecture tradeoffs of REST vs gRPC" \
  "hello there how are you" \
  "rephrase this sentence to be clearer" \
  "summarize this article in two sentences" \
  "extract the name email and date from this text" \
  "compare the pros and cons of monolith vs microservices" \
  "good morning"
do
  curl -s -X POST http://localhost:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{\"messages\":[{\"role\":\"user\",\"content\":\"$prompt\"}],\"max_tokens\":80}" \
    > /dev/null
done
```

## 2. Capture the three screenshots

On macOS, `Cmd+Shift+4` then `Space` then click a window captures it cleanly. On Linux, GNOME Screenshot does the same with `PrtScn`.

| Path | URL | What to show |
|---|---|---|
| `dashboard.png` | `http://localhost:8000/dashboard` | The overview page — KPIs, the two charts, and the recent-requests table. |
| `playground.png` | `http://localhost:8000/dashboard/playground` | Type a real prompt so the live cost preview shows numbers; expand the response after running. Whole window. |
| `cost.png` | `http://localhost:8000/dashboard/cost` | The cost-breakdown page with the daily chart visible. |

Target ~1400-1600 px wide. PNG. Crop to just the browser viewport (no OS chrome).

## 3. Commit them

```bash
git add docs/screenshots/dashboard.png \
        docs/screenshots/playground.png \
        docs/screenshots/cost.png
git commit -m "docs: add dashboard screenshots"
```

The README image links work immediately on GitHub once the images are committed.
