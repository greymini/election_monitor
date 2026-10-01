# NEXT_STEPS.md — lean plan

Three steps (after a quick Step 0) to a deployed, working dashboard. Everything else is listed under "Later" and is optional.

**Where things stand:** the code exists in two places that split apart. The zip from `C:\dev\giridih-monitor` (top commit `7cee208`) has almost everything. The last two commits (`caf6afe`: `scripts/dev_stack.py`, the local full stack; `df1979e`: the every-route test) were made in the old OneDrive copy on older code, and travel as patch files in `patches_from_onedrive/`. Step 0 brings them in. After that, two endpoints are known to fail against the database: `/summary` (Overview) and `/booths/{uid}/card` (booth drawer). Step 1 fixes them.

---

## Before you start: move the code

**On the old laptop: no code changes.** First export the two commits from the OneDrive copy:
```powershell
cd "C:\Users\CC543VB\OneDrive - EY\Documents\election_monitor"
git format-patch e1b9263..df1979e -o C:\dev\giridih-monitor\patches_from_onedrive
```
Then zip. Zip the `giridih-monitor` folder as it is, **including** the hidden `.git` folder. Leave out `node_modules`, `.venv`, `dist`, `__pycache__`, `.pytest_cache`, `raw`, `ocr`, `backups` and `.env` (`.env` holds secrets; you'll make a fresh one). The zip should be a few MB; hundreds of MB means `node_modules` or `.venv` got in.

**On the personal machine** (Git, Python 3.11, Node 20 and Cursor installed), unzip to `C:\dev\election-monitor`, then:
```powershell
cd C:\dev\election-monitor
git status        # shows NEXT_STEPS.md and patches_from_onedrive/ as untracked; everything else clean
git log --oneline -3   # top commit should be 7cee208
dir patches_from_onedrive   # two .patch files
git config user.email "kunalkumar4126@gmail.com"
```
Copy this `NEXT_STEPS.md` and the three v2 docs (`README.md`, `Giridih_AC32_Election_Monitor_HLD.md`, `Giridih_AC32_Election_Monitor_LLD.md`) into the folder, replacing the old ones, then commit:
```powershell
git add NEXT_STEPS.md README.md Giridih_AC32_Election_Monitor_HLD.md Giridih_AC32_Election_Monitor_LLD.md
git commit -m "docs: v2 HLD, LLD, README and lean next steps"
git ls-files | Select-String -Pattern "\.env$","raw/","ocr/"   # must print nothing
```
Set up the environment:
```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
dir requirements*        # then pip install -r each file listed
cd web; npm install; cd ..
copy .env.example .env   # set JWT_SECRET to: python -c "import secrets; print(secrets.token_hex(32))"
```
Then open `C:\dev\election-monitor` in Cursor.

### Check it runs (once, before Cursor touches anything)

**A. Frontend only, mock data (no backend needed).** In a PowerShell window:
```powershell
cd C:\dev\election-monitor\web
$env:VITE_FIXTURES="1"; npm run dev
```
Open http://localhost:5173/ and click around. Stop with Ctrl + C.

**B. Full stack: backend on a local database, frontend talking to it.** This only works after Step 0, because `dev_stack.py` arrives with the patches. Use two PowerShell windows.

Window 1 (backend):
```powershell
cd C:\dev\election-monitor
.\.venv\Scripts\Activate.ps1
python scripts/dev_stack.py
```
The first run takes a few minutes (it builds the database and loads the mock data). It prints the login users and passwords and the API address when ready. Leave this window running. Check http://localhost:8000/health in the browser; it should answer OK.

Window 2 (frontend), a **new** window so the fixtures setting from A isn't carried over:
```powershell
cd C:\dev\election-monitor\web
npm run dev
```
Open http://localhost:5173/ and log in with one of the users from window 1.

**Expected right now:** most pages work, but the **Overview page and the booth drawer show an error**. Those are the two broken endpoints Step 1 fixes. Anything else broken, note it down for Cursor.

Stop both with Ctrl + C when done.

In Cursor, create `.cursor/rules/project.mdc`:
```markdown
---
description: Project rules
alwaysApply: true
---
Read PROGRESS.md before changing anything. Keep changes small; don't add features or
infrastructure that weren't asked for. Never store individual voter data anywhere. Missing data
shows as "—", never 0. Run long-lived servers in the background. Don't say something works unless
you ran it; quote the output. Commit after each change.
```

---

## Step 0 — Bring in the two commits from the OneDrive copy

Send to Cursor (Agent mode):
```
Read PROGRESS.md. The folder patches_from_onedrive/ holds two commits (caf6afe: scripts/dev_stack.py,
a local full stack on embedded PostgreSQL loaded through the real loaders; df1979e: a route x role
test against a real database, plus fixes). They were made in an older copy of this repo, where
the app lived in a giridih-monitor/ subfolder, so paths need the "giridih-monitor/" prefix removed.
They were built on older code: this repo already has later work (N1, N2, N4, the frontend fixes,
the new Overview). Port them onto the current code, keeping the current code wherever the two
overlap, and drop any fix the current code already has. Don't change anything else.
Then run the test suite and python scripts/dev_stack.py (in the background) and show me the output.
Commit as two commits, record the port in PROGRESS.md, and delete patches_from_onedrive/.
Finally, check README.md, the HLD and the LLD (v2) against the code: fix any command, file name or
requirements file name that doesn't match, and commit. Don't change their content otherwise.
```

**Done when:** `python scripts/dev_stack.py` starts, the tests pass, and `patches_from_onedrive/` is gone.

---

## Step 1 — Dashboard working against the real local database

Send to Cursor (Agent mode):
```
Read PROGRESS.md. Fix the two endpoints still failing against the dev stack: /summary and
/booths/{uid}/card (stale column names after migration 0015). Remove their xfail markers once
fixed. Then start scripts/dev_stack.py in the background, point the frontend at it (no
VITE_FIXTURES), and confirm the Overview and the booth drawer load. Commit.
```
Then check yourself: run `python scripts/dev_stack.py`, then `cd web; npm run dev` in a second window, log in, click through Overview, Booths, Map and a booth drawer.

**Done when:** those pages show data from the local database with no errors.

---

## Step 2 — Every page opens without errors

Send to Cursor (new chat):
```
Read PROGRESS.md. Add one simple Playwright smoke test: log in as admin, open every page in the
navigation for AC 32, and fail on any console error, any failed API request, or any visible
"undefined", "NaN" or "{{". Run it against the dev stack. Fix whatever it finds. Commit and show me
the output. Don't add other tests.
```
(If Chromium won't download, add `channel: 'msedge'` in `web/playwright.config.ts`.)

**Done when:** the smoke test passes and you've clicked through every page yourself.

---

## Step 2.5 — Keep the docs in step with the code

The v2 README, HLD and LLD were added at setup. After Steps 1 and 2 have changed code, send to Cursor (new chat):
```
Read PROGRESS.md. Check README.md, Giridih_AC32_Election_Monitor_HLD.md (v2),
Giridih_AC32_Election_Monitor_LLD.md (v2) and RUN.md against the code as it is now. Fix any
command, file name, route, requirements file or status line that no longer matches, and update
the LLD's "Open items" section. Don't change any code and don't restructure the docs. Commit.
```

**Done when:** a new reader could set up and run the project from README.md alone.

---

## Step 3 — Deploy (Supabase + Railway + Vercel)

**3a. Prepare.** Send to Cursor (new chat):
```
Read PROGRESS.md. Prepare deployment: frontend on Vercel, API on Railway, Postgres on Supabase.
Add web/vercel.json with SPA rewrites; read the API address from VITE_API_BASE; CORS allow-list
from CORS_ORIGINS; API listens on $PORT. DATABASE_URL must work with the Supabase Session
pooler over SSL (psycopg prepare_threshold=None, small pool). Add a script that loads the same
mock data the dev stack uses into any DATABASE_URL. Write a short DEPLOY.md with the steps and
the env vars for each service. Keep local development working. Commit.
```

**3b. Supabase** (free plan is fine for a demo):
- Create a project (Singapore region). Database → Extensions → enable `vector`.
- Copy the **Session pooler** connection string (Project Settings → Database).
- From your laptop, set `DATABASE_URL` to it in `.env`, run migrations and the mock-data loader, then run the frontend locally against it to check.

**3c. Railway:** new project from the repo, API service, region Singapore, env vars from `DEPLOY.md`.

**3d. Vercel:** import the repo, root directory `web`, set `VITE_API_BASE` to the Railway URL, deploy. Add the Vercel URL to `CORS_ORIGINS` on Railway.

**Done when:** you can log in on the Vercel URL and click through every page.

---

## Later (only when needed)

- Real Giridih Form 20 and polling-station list, loaded and checked (margin 1.85%; one booth matches its PDF page).
- Verify the five other seats against ECI and clear the "unverified" badges.
- Mock booth-level data for the other five seats.
- Remaining audit items (e.g. login rate limit).
- Map tiles: switch `VITE_TILE_URL` to MapTiler or similar before regular team use.
- Supabase Pro before real campaign use (free projects pause after a week idle).
- Anything from FRONTEND_HARDENING.md not covered above: response models, generated types, the full test matrix.

Check every number on the dashboard against its source before relying on it.
