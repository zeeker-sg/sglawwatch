# CLAUDE.md - Sglawwatch-Zeeker Project Development Guide

This file provides Claude Code with project-specific context and guidance for developing this project.

## Project Overview

**Project Name:** sglawwatch-zeeker
**Database:** sglawwatch-zeeker.db
**Purpose:** Database project for sglawwatch-zeeker data management

## Development Environment

This project uses **uv** for dependency management with an isolated virtual environment:

- `pyproject.toml` - Project dependencies and metadata
- `.venv/` - Isolated virtual environment (auto-created)
- All commands should be run with `uv run` prefix

### Dependency Management
- **Add dependencies:** `uv add package_name` (e.g., `uv add requests pandas`)
- **Install dependencies:** `uv sync` (automatically creates .venv if needed)
- **Common packages:** requests, beautifulsoup4, pandas, lxml, pdfplumber, openpyxl

## Development Commands

### Quick Commands
- `uv run zeeker add RESOURCE_NAME` - Add new resource to this project
- `uv run zeeker add RESOURCE_NAME --fragments` - Add resource with document fragments support
- `uv run zeeker build` - Build database from all resources in this project
- `uv run zeeker deploy` - Deploy this project's database to S3

### Testing This Project
- `uv run pytest` - Run tests (works without API keys - uses mocks)
- `uv run pytest test_headlines.py -v` - Run specific headlines tests
- Check generated `sglawwatch-zeeker.db` after build
- Verify metadata.json structure

### Working with Dependencies
When implementing resources that need external libraries:
1. **First add the dependency:** `uv add library_name`
2. **Then use in your resource:** `import library_name` in `resources/resource_name.py`
3. **Build works automatically:** `uv run zeeker build` uses the isolated environment

## Resources in This Project

### `headlines` Resource
- **Description:** Keep in touch with the latest legal news articles collated from The Straits Times, The Business Times, Lianhe Zaobao and TODAY.
- **File:** `resources/headlines.py`
- **Schema:** Check `resources/headlines.py` fetch_data() for current schema

### `about_singapore_law` Resource
- **Description:** About Singapore Law data
- **File:** `resources/about_singapore_law.py`
- **Type:** Fragment-enabled (creates two tables: `about_singapore_law` and `about_singapore_law_fragments`)
- **Schema:** Check `resources/about_singapore_law.py` both fetch_data() and fetch_fragments_data() functions


## Schema Notes for This Project

### Important Schema Decisions
- Document any project-specific schema choices here
- Note field types that are critical for this project's data
- Record any special data handling requirements

### Common Schema Issues to Watch
- **Dates:** Use ISO format strings like "2024-01-15"
- **Numbers:** Use float for prices/scores that might have decimals
- **IDs:** Use int for primary keys, str for external system IDs
- **JSON data:** Use dict/list types for complex data structures

### Fragment Resources
If using fragment-enabled resources (created with `--fragments`):
- **Two Tables:** Each fragment resource creates a main table and a `_fragments` table
- **Schema Freedom:** You design both table schemas through your `fetch_data()` and `fetch_fragments_data()` functions
- **Linking:** Include some way to link fragments back to main records (your choice of field names)
- **Use Cases:** Large documents, legal texts, research papers, or any content that benefits from searchable chunks

## Project-Specific Notes

### Data Sources
- Document where this project's data comes from
- Note any API endpoints, file formats, or data constraints
- Record update frequencies and data refresh patterns

### Business Logic
- Document any special business rules for this project
- Note relationships between resources
- Record any data validation requirements

## Environment Variables Setup

This project requires several environment variables for API integrations and S3 deployment.

### Quick Setup
1. **Copy the template:** `cp .env.example .env`
2. **Edit .env file** with your actual API keys and credentials
3. **Never commit .env** - it's already in .gitignore

### Required Environment Variables

#### For Headlines Resource (API Integration)
```bash
# Jina Reader API - for extracting article content
# Get free API key at: https://jina.ai/reader/
JINA_API_TOKEN=your_jina_api_token_here

# OpenAI API - for generating article summaries  
# Get API key at: https://platform.openai.com/api-keys
OPENAI_API_KEY=your_openai_api_key_here
```

#### For S3 Deployment (Zeeker Deploy)
```bash
# Required for: uv run zeeker deploy
S3_BUCKET=your-bucket-name
AWS_ACCESS_KEY_ID=your_aws_access_key
AWS_SECRET_ACCESS_KEY=your_aws_secret_key

# Optional: for non-AWS S3 services (Contabo, DigitalOcean, etc.)
S3_ENDPOINT_URL=https://your-s3-endpoint.com
```

### Setting Environment Variables

#### Method 1: .env file (Recommended)
```bash
# Copy template and edit
cp .env.example .env
# Edit .env with your actual values
```

#### Method 2: Shell export (temporary)
```bash
export JINA_API_TOKEN="your-token"
export OPENAI_API_KEY="your-key"
export S3_BUCKET="your-bucket"
# etc...
```

#### Method 3: Inline with uv run
```bash
JINA_API_TOKEN=your-token uv run zeeker build
```

#### Method 4: System environment variables
Add to your shell profile (~/.bashrc, ~/.zshrc, etc.)

### Testing with Environment Variables

#### Without Real API Keys (Recommended)
```bash
# Tests use mocks - no API keys needed
uv run pytest test_headlines.py -v
```

#### With Real API Keys (Integration Testing)
```bash
# Option 1: Load from .env file manually
uv run python -c "from dotenv import load_dotenv; load_dotenv(); import subprocess; subprocess.run(['pytest', 'test_headlines.py', '-v'])"

# Option 2: Set environment variables inline
JINA_API_TOKEN=your-token OPENAI_API_KEY=your-key uv run pytest test_headlines.py -v

# Option 3: Export in shell first
export JINA_API_TOKEN=your-token
export OPENAI_API_KEY=your-key
uv run pytest test_headlines.py -v
```

#### S3 Deployment Testing
```bash
# Option 1: Load from .env file manually
uv run python -c "from dotenv import load_dotenv; load_dotenv(); import subprocess; subprocess.run(['zeeker', 'deploy'])"

# Option 2: Export variables first
export S3_BUCKET=your-bucket
export AWS_ACCESS_KEY_ID=your-key
export AWS_SECRET_ACCESS_KEY=your-secret
uv run zeeker deploy

# Option 3: Set variables inline
S3_BUCKET=your-bucket AWS_ACCESS_KEY_ID=your-key AWS_SECRET_ACCESS_KEY=your-secret uv run zeeker deploy
```

### Deployment Notes
- Database deploys to: `s3://your-bucket/latest/sglawwatch.db`
- Assets deploy to: `s3://your-bucket/assets/databases/sglawwatch/`
- Supports AWS S3 and S3-compatible services
- Configure S3_ENDPOINT_URL for non-AWS services

## Team Notes

*Use this section for team-specific development notes, decisions, or reminders*

---

## Build Monitoring Guide (for AI agents)

This section helps AI agents monitoring the build pipeline interpret log output correctly.

### Resources and what "no data returned" means

| Resource | Source | Proxy required? | Normal "no data" cause | Abnormal "no data" cause |
|----------|--------|-----------------|----------------------|------------------------|
| `headlines` | Singapore LawWatch RSS feed | No | All headlines already imported (slow news day) | RSS feed unreachable, Jina Reader 422 errors |
| `commentaries` | Singapore LawWatch Commentaries RSS + PDF via Docling | Yes (for PDF extraction only) | All commentaries already imported. RSS works but PDF extraction may fail | Docling server down (port 5001), proxy timeout for PDF URLs |
| `about_singapore_law` | Singapore LawWatch About section | Yes | All articles already scraped | Proxy down → `RetryError[ProxyError]` |

### Normal yield expectations

- **headlines:** 0–10 new per day (typical: 2–5 on weekdays, 0 on weekends/holidays). Skips 40+ ads and 60+ old headlines per run.
- **commentaries:** 0–3 new per day. 120+ already-imported entries skipped. PDF extraction adds ~10–80s per new commentary.
- **about_singapore_law:** 0 new most days — articles are rarely added. ~46 total articles, stable.

### How to tell a healthy skip from a failure

- **Healthy skip:** Duration 0.1–5s, log shows "Skipped N already-imported entries" or "Skipping (published before last_update)"
- **Failed skip (proxy):** Duration 20–200s, log shows `RetryError[ProxyError]` or `Failed to fetch sitemap`. about_singapore_law commonly affected.
- **Failed skip (docling):** Duration 60–300s, log shows `docling failed: RetryError[...ConnectError]`. Commentaries affected.
- **Failed skip (Jina):** Duration 5–30s, log shows `422 Unprocessable Entity` from Jina Reader. Rare, falls back to plain text.

### Current DB stats (as of Jul 2026)

- headlines: ~833 rows
- commentaries: ~201 rows
- about_singapore_law: ~46 rows

### Build duration expectations

- Fast run (all up to date): 5–10s total
- Run with new headlines: 20–30s
- Run with new commentaries (PDF extraction): 80–180s
- Run with about_singapore_law: 5–10s (or 200s+ if proxy is timing out)
