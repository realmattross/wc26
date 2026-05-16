# Health dashboard — Netlify deployment

Personal Apple Health dashboard, deployed as a static site to Netlify
with a tiny serverless function for live Claude Q&A.

## What's here

```
health-site/
├── public/
│   ├── index.html          ← the dashboard (static, never rebuilt)
│   ├── data.json           ← regenerated daily by build_health_site.py
│   ├── insights.json       ← regenerated daily — Claude's weekly trend paragraph
│   └── robots.txt          ← blocks search engines
├── netlify/functions/
│   └── ask.js              ← /.netlify/functions/ask — live Claude chat
├── netlify.toml            ← Netlify config
└── package.json            ← Node engine pin for the function
```

The build script lives at `scripts/build_health_site.py` (one level up,
in the main Jeeves repo). It runs daily via the launchd plist in
`launchd/com.mattross.jeeves-health-site.plist`.

## First-time setup

### 1. Push this repo to GitHub

Netlify deploys from a git repo, so you need one. From the Jeeves repo
root:

```bash
# If the Jeeves repo isn't already on GitHub:
gh repo create jeeves --private --source=. --remote=origin --push

# Otherwise just make sure 'git push' works from this folder.
```

The health-site folder lives inside the main Jeeves repo. Netlify will
deploy just the `health-site/` subdirectory — configured in step 2.

### 2. Connect Netlify to the repo

1. Sign in at https://app.netlify.com (free tier is plenty).
2. **Add new site → Import an existing project** → pick GitHub →
   pick your `jeeves` repo.
3. On the configuration screen:
   - **Base directory:** `health-site`
   - **Build command:** (leave empty — the build runs on Matt's Mac)
   - **Publish directory:** `health-site/public`
   - **Functions directory:** `health-site/netlify/functions`
4. Click **Deploy site**. It'll fail the first time (no data.json yet) —
   we fix that in step 4.

### 3. Add the Anthropic API key as a Netlify env var

In the Netlify dashboard for the new site:
**Site settings → Environment variables → Add a variable**

- **Key:** `ANTHROPIC_API_KEY`
- **Value:** your `sk-ant-…` key
- Scope: all deploy contexts

Without this, the live chat will respond with a configuration error
(the page itself still loads fine; only chat is affected).

### 4. Run the first build

From the Jeeves repo root on the Mac:

```bash
cd ~/Code/jeeves
python scripts/build_health_site.py
```

This regenerates `health-site/public/data.json` and `insights.json`.
Verify them:

```bash
ls -la health-site/public/
# you should see data.json (~100-300 kB) and insights.json (~2 kB)
```

Open `health-site/public/index.html` in your browser to preview locally
(everything works except the live chat, which needs the deployed
function).

If it looks right, push:

```bash
git add health-site/
git commit -m "health dashboard: initial deploy"
git push
```

Netlify auto-deploys within ~30 seconds. The site URL is on the Netlify
dashboard — something like `https://chic-baklava-a1b2c3.netlify.app`.

### 5. Rename the site to something less guessable (optional)

In Netlify: **Site settings → Site information → Change site name**.
Pick something only you'd guess — Netlify URLs are technically public,
but with `robots.txt` blocking crawlers and an obscure name, it's
effectively private.

### 6. Wire up the daily rebuild

Install the launchd plist that runs the build script at 06:30 every day:

```bash
cp launchd/com.mattross.jeeves-health-site.plist \
   ~/Library/LaunchAgents/com.mattross.jeeves-health-site.plist
launchctl load ~/Library/LaunchAgents/com.mattross.jeeves-health-site.plist
```

Verify it loaded:

```bash
launchctl list | grep jeeves-health-site
```

The job runs `python scripts/build_health_site.py --push`, which:
1. Reads the freshest Apple Health export (90-day history)
2. Regenerates `data.json`
3. Asks Claude for the weekly insights paragraph, writes `insights.json`
4. Commits + pushes if either file changed
5. Netlify rebuilds the deploy automatically

### 7. Confirm `ANTHROPIC_API_KEY` is also in `.env` on the Mac

The build script reads it to generate insights at build time. If
`.env` already has it (for the main Jeeves brain), you're done. If not:

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> ~/Code/jeeves/.env
```

## Daily test

After the first scheduled run (next 06:30), check:

```bash
tail -n 30 ~/.jeeves-logs/health-site.out
tail -n 30 ~/.jeeves-logs/health-site.err
```

You should see:
```
[build] reading health data…
[build] wrote .../data.json (XXXXX bytes)
[build] generating insights via Claude…
[build] wrote .../insights.json
[push] pushed daily rebuild
```

## Manual rebuild

If you want a fresh deploy right now (e.g. after editing the persona
in `build_health_site.py`):

```bash
cd ~/Code/jeeves
python scripts/build_health_site.py --push
```

## Costs

- **Netlify:** free tier covers 100GB bandwidth + 125k function invocations / month. You'd need to ask thousands of chat questions to come close.
- **Claude API:** insights run = ~1k input tokens, ~400 output tokens per day = roughly £0.01/day. Chat questions are ~20-60kB input each, so ~£0.01-0.05 per chat depending on conversation length. Capped by usage, not by Netlify.

## What's deployed vs. what stays local

- **Deployed (Netlify):** the static HTML, the daily JSON snapshot, and the chat function (which has the API key).
- **Local-only:** everything else — the raw Apple Health export files, the editable in-app dashboard at `localhost:8765/health/dashboard`, the voice agent, all of Jeeves's other features.

The deployed dashboard is read-only. To edit a metric override, do it
in the local dashboard (overrides live in `~/.jeeves-health-overrides.json`)
and they'll be reflected in the next daily rebuild.

## Troubleshooting

**Site shows "Couldn't load health data".** The build script hasn't been
run yet, or `data.json` is missing from the repo. Run
`python scripts/build_health_site.py` and push.

**Chat says "ANTHROPIC_API_KEY is not set".** Add it in the Netlify
dashboard under Site settings → Environment variables, then trigger a
new deploy (Deploys → Trigger deploy → Clear cache and deploy).

**Insights paragraph is the fallback text.** The `ANTHROPIC_API_KEY` in
your local `.env` isn't being picked up by the launchd job. Check
`~/.jeeves-logs/health-site.err` — it'll tell you whether the call
failed or the key was missing.

**Daily rebuild isn't running.** Check `launchctl list | grep jeeves-health-site`.
If the exit status (the second column) is non-zero, read
`~/.jeeves-logs/health-site.err` for the error.
