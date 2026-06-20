# ⚽ WC26 — World Cup 2026 Hub

Auto-updating World Cup 2026 scores, groups, fixtures, teams and stadiums.  
Rebuilds every hour via GitHub Actions → deploys to Netlify automatically.

**Live site:** https://world-cup-roscoe.netlify.app

---

## How it works

```
GitHub Actions (every hour)
  → scripts/build.py fetches live data from worldcup26.ir
  → injects into template.html
  → outputs dist/index.html
  → deploys to Netlify
```

---

## One-time setup (5 minutes)

### Step 1 — Create the GitHub repo

```bash
# On your machine (or just create via github.com/new)
git init wc26
cd wc26
# Copy these files in, then:
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/realmattross/wc26.git
git push -u origin main
```

### Step 2 — Get your Netlify Site ID

1. Go to https://app.netlify.com
2. Click your **world-cup-roscoe** site
3. Go to **Site configuration → General**
4. Copy the **Site ID** (looks like: `a1b2c3d4-...`)

### Step 3 — Get your Netlify Personal Access Token

1. Go to https://app.netlify.com/user/applications
2. Click **New access token**
3. Name it `GitHub Actions WC26`
4. Copy the token (shown once only — save it)

### Step 4 — Add secrets to GitHub

1. Go to your repo on GitHub
2. **Settings → Secrets and variables → Actions → New repository secret**
3. Add these three secrets:

| Secret name | Value |
|---|---|
| `NETLIFY_AUTH_TOKEN` | Your Netlify personal access token |
| `NETLIFY_SITE_ID` | Your Netlify site ID |
| `WC26_API_TOKEN` | `TH1cqV6bmZcmtV3SWDtH1N1roCEcAGpB7V` |

### Step 5 — Trigger first deploy

Go to **Actions → Build & Deploy WC26 → Run workflow**

That's it. Every hour from now on it rebuilds automatically.

---

## Manual rebuild

Any of these trigger a rebuild:
- Push anything to `main`
- Go to Actions tab → Run workflow manually
- Wait for the hourly cron

---

## Files

```
wc26/
├── template.html              ← App shell with __PLACEHOLDER__ variables
├── scripts/
│   └── build.py               ← Fetches API + builds dist/index.html
├── .github/
│   └── workflows/
│       └── deploy.yml         ← GitHub Action (hourly schedule)
├── netlify.toml               ← Netlify config
├── _redirects                 ← SPA routing
└── dist/                      ← Built output (gitignored, Netlify reads this)
    └── index.html
```

---

## Schedule

The action runs **every hour on the hour** (`0 * * * *`).  
During knockout stages with multiple daily games this keeps scores fresh.  
After the tournament ends (19 Jul) you can change to daily or disable.
