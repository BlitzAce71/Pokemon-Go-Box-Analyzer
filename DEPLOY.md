# Public Sharing / Deployment

This app can be shared outside your home network in two main ways.

## Option 1: Quick Share from Your Own Computer (Cloudflare Tunnel)

Use this when you want a public URL quickly without deploying code.

1. Run analyzer locally:
   ```bash
   python -m pogo_box_analyzer serve-web --host 127.0.0.1 --port 8787
   ```
2. In another terminal, run cloudflared tunnel:
   ```bash
   cloudflared tunnel --url http://127.0.0.1:8787
   ```
3. Share the generated `https://...trycloudflare.com` URL.

Notes:
- URL is temporary unless you configure a named tunnel.
- Your computer must stay on while others use the app.

## Option 2: Deploy to Render (recommended for stable sharing)

The repo includes `render.yaml` (Blueprint config).

### A) Put this project in GitHub

If this folder is not already a git repo:

```bash
git init
git add .
git commit -m "PoGo Box Analyzer web deploy"
```

Create an empty GitHub repo, then push:

```bash
git branch -M main
git remote add origin <your-github-repo-url>
git push -u origin main
```

### B) Deploy on Render

1. Open Render dashboard.
2. Click **New** -> **Blueprint**.
3. Connect/select your GitHub repo.
4. Render will detect `render.yaml` and create the web service.
5. Wait for first deploy to finish, then open the generated `https://...onrender.com` URL.

Current app settings:
- Build: `pip install -r requirements.txt && pip install .`
- Start: `python -m pogo_box_analyzer serve-web --host 0.0.0.0 --port $PORT`
- Health check: `/health`
- Python version pinned to 3.12 via `.python-version` / `runtime.txt`

Notes:
- Free tiers may sleep when idle and take a cold-start delay.
- App filesystem is ephemeral; this app is fine because outputs are downloaded immediately as CSV.

## Security

- Do not upload private screenshots to an untrusted public deployment.
- If you share publicly, consider adding authentication at a reverse proxy or platform layer.
