# PropVenture

Multi-agent AI research assistant for Indian home buyers. See `PRD.md` for full product spec and `design.md` for the UI design system.

## Structure

```
agents/            search agent, credibility agent, price-model agent
orchestrator/       coordinates agents, validates output before persisting
data_pipeline/      MahaRERA ingestion, listing scraping, review/news ingestion
models/             price-tier deviation model (hierarchical + quantile regression)
security/           input sanitization, output validation (prompt injection defense)
mlops/               MLflow + Evidently tracking
frontend/           Streamlit app
tests/
```

## Setup

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt --break-system-packages
cp .env.example .env  # fill in keys
```

## Run

```bash
streamlit run frontend/app.py
```

## Status

Architecture and USP finalized. Core build in progress — see `PRD.md` section 5 for current MVP scope.
