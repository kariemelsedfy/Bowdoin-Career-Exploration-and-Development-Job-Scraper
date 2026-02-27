# Bowdoin Career Exploration and Development Job Tracker

FastAPI + PostgreSQL backend for tracking job postings from a curated employer list.

## Goal

Start with a reliable CSV enrichment pipeline:
- Input: employer list CSV.
- Output: CSV with each employer's careers/jobs URL plus confidence metadata.
- Processing model: concurrent workers, one employer per task/session.

Then expand to:
- Persistent run history in PostgreSQL.
- On-demand scrape runs from API.
- Frontend dashboard later.

## Architecture

- API: FastAPI (`backend/main.py`)
- Database: PostgreSQL + SQLAlchemy models
- Enrichment: Anthropic Claude Sonnet 4.5 on AWS Bedrock URL resolver + CSV worker (`scripts/enrich_employers_csv.py`)
- Deployment (local): `docker-compose.yml`

## Repo Layout

```text
backend/
  app/
    api/
    core/
    db/
    models/
    schemas/
    services/
    workers/
  main.py
scripts/
  enrich_employers_csv.py
data/
  raw/
  processed/
tests/
alembic/
```

## Quick Start

1. Create a virtual environment and install dependencies:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

2. Copy env template and set your Bedrock credentials/model:
```bash
cp .env.example .env
```
Set `AWS_BEARER_TOKEN_BEDROCK` (or AWS credentials/profile), `BEDROCK_REGION`, and `BEDROCK_MODEL_ID` (for example, `us.anthropic.claude-sonnet-4-5-20250929-v1:0`).
For Anthropic models, your AWS admin must submit Anthropic use-case details once in Bedrock Model access.

3. Start PostgreSQL:
```bash
docker compose up -d postgres
```

4. Start API:
```bash
uvicorn backend.main:app --reload
```

5. Run CSV enrichment:
```bash
python scripts/enrich_employers_csv.py \
  --input employers.csv \
  --output data/processed/employers_with_urls.csv \
  --employer-column employer \
  --concurrency 5
```

## API Endpoints

- `GET /api/health`
- `POST /api/runs/enrichment` (stub for queue-backed run execution)

## Data Model (initial)

- `Employer`: canonical employer info and careers URL.
- `Job`: individual job postings.
- `EnrichmentRun`: run metadata/status.
- `EnrichmentResult`: per-employer result from an enrichment run.

## Notes on Accuracy

Current scaffold uses Anthropic Claude on Bedrock for best-effort URL selection. For production-quality accuracy, add:
- Search API candidate generation.
- URL fetch/validation checks.
- Manual review queue for low-confidence results.

## Next Milestones

1. Wire Alembic migrations.
2. Persist enrichment runs/results to PostgreSQL.
3. Add scraping adapters per ATS/provider.
4. Add frontend dashboard with Tailwind.
