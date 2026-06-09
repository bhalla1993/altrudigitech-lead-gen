# AI Agent Context (project summary)

Purpose
- Provide a compact, machine- and human-readable project context so code agents (Copilot Chat, CI bots) can quickly understand the repository.

Where to find it
- This file: `docs/AI_AGENT_CONTEXT.md`
- Machine-readable summary: `docs/ai-agent-context.yml`

Quick summary
- Name: altrudigitech-lead-gen
- Purpose: Scrape and analyze websites to generate short auditing suggestions and outreach copy for leads.
- Language: Python (single-package `app`)
- Frontend: lightweight SPA using `static/index.html`, `static/app.js`, `static/style.css`
- Data: `data/audits/`, `data/screenshots/`
- Tests: `tests/`

Key files and responsibilities
- app/main.py — CLI / entrypoint
- app/analyzer.py — scoring & heuristics
- app/scraper.py — website fetching + screenshot capture
- app/db.py — simple persistence layer
- static/style.css — frontend styles (variables and primary selectors)
- scripts/ — runnable helpers (reanalyze_all.py, rescrape_and_reanalyze.py)

Developer notes for agents
- Read `README.md` first to learn usage and deploy steps.
- Use `tests/` and `pytest -q` to run tests.
- Do not attempt to write secrets into `docs/ai-agent-context.yml`; list only env variable names.
- If code references a CSS class or id used in JS/templates, check `static/style.css` and update context if you add/remove selectors.

Maintenance checklist (for humans)
- When adding/removing folders or important files, update both `docs/ai-agent-context.yml` and this README.
- Run the generator script (optional) after refactors to refresh lists.
- Add a short entry to `docs/CONTEXT_CHANGELOG.md` for major structural changes.

Questions for you
- Do you want a small generator script that auto-populates `ai-agent-context.yml` from the repo (I can provide it)?
- Would you like a CI job that fails if the generated context diverges from the committed file?