# AltruDigiTech Lead‑Gen Engine — FastAPI microservice

A small backend service to scan business websites, analyze them with AI, score their design/usability, and store leads for outreach.

This repository contains the core MVP service used by the AltruDigiTech lead pipeline: scraper, analyzer, database, and API.

## Quick start

Prerequisites:

- Python 3.10+ or 3.11
- `pip` and `virtualenv` (recommended)
- Playwright browsers (see install step)

Install dependencies and Playwright browsers:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install
```

Create a local `.env` (copy from `.env.example`) and set `OPENAI_API_KEY` or other provider keys. Do NOT commit secrets.

---

## Features

- Scrape a website (desktop + mobile screenshots and HTML)
- Analyze screenshots with an AI model to produce a 1–10 score and short reasoning
- Store leads and screenshots in `data/` and metadata in SQLite
- API to scan, list, mark contacted, and export leads

---

## Project layout

Key paths:

- `app/` — application code (FastAPI app, scraper, analyzer, DB models)
- `data/` — runtime data (screenshots, SQLite DB)
- `requirements.txt` — Python dependencies
- `tests/` — basic unit tests

---

## Configuration

Environment variables (populate `.env` locally):

- `OPENAI_API_KEY` — API key for AI provider
- `DATABASE_URL` — optional SQLAlchemy URL (defaults to SQLite `data/leads.db`)

---

## Running locally

Start the API server in development:

```bash
uvicorn app.main:app --reload
```

Example: scan a URL with `curl`:

```bash
curl -X POST "http://localhost:8000/scan-url" -H "Content-Type: application/json" \
  -d '{"website_url":"https://example.com","use_ai":false}'
```

---

## API endpoints (summary)

- `POST /scan-url` — scan a single URL (body: `website_url`, optional flags)
- `POST /batch-scan` — scan multiple URLs
- `GET /leads` — list leads
- `GET /leads/{id}` — lead details
- `POST /leads/{id}/contact` — mark contacted
- `GET /export` — export leads CSV

Refer to the code in `app/main.py` for parameter details and example requests.

---

## Tests

Run tests with `pytest`:

```bash
pytest -q
```

Note: some tests may require Playwright browsers and/or network access; run them in an environment with those available.

---

## Security and Secrets

- `.env` is ignored by `.gitignore`. Keep secrets out of the repository.
- Revoke and rotate any keys accidentally committed (you already removed an exposed key).

---

## Contributing

Open an issue or submit a PR. For major changes, please open an issue first to discuss the design.

---

## License

This project has no license file in the repo. Add a `LICENSE` if you want to make it open source.

