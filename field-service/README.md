# Election Monitor — Field Modules (working name: Project Pulse)

This repo adds two field modules to the Jharkhand Election Monitor: an expense ledger and ground reports, with a Telegram bot and web screens. It is a standalone service, to be merged with the core platform later.

**Start with [HANDOFF.md](HANDOFF.md).** It covers what is built, how to run it on Windows, macOS or Linux, how to connect Telegram, and how to merge it with the core.

Quick look, no `.env` or Telegram needed (Python 3.11 and Node.js 18+):

```
python -m venv .venv
.venv\Scripts\activate               # macOS/Linux: source .venv/bin/activate
pip install -c constraints.txt -e ".[dev]"
python -m scripts.demo               # then open http://127.0.0.1:8787/
```

More documentation:

| File | What it is for |
|---|---|
| `docs/MERGE.md` | The merge contract |
| `docs/RUNBOOK.md` | Operating the service |
| `docs/DEMO.md` | The demo walkthrough |
| `Field_Modules_HLD.md`, `Field_Modules_LLD.md` | The design |
| `CLAUDE.md` | Working rules for Claude Code |

`README (1).md` describes the existing platform and is background only.
