# Sglawwatch-Zeeker Database Project
[![Sync Headlines Database](https://github.com/zeeker-sg/sglawwatch/actions/workflows/sync-headlines.yml/badge.svg)](https://github.com/zeeker-sg/sglawwatch/actions/workflows/sync-headlines.yml)


A Zeeker project for managing the sglawwatch-zeeker database.

## Getting Started

1. Add dependencies for your data sources:
   ```bash
   uv add requests beautifulsoup4  # Example: web scraping dependencies
   ```

2. Add resources:
   ```bash
   uv run zeeker add my_resource --description "Description of the resource"
   ```

3. Implement data fetching in `resources/my_resource.py`

4. Build the database:
   ```bash
   uv run zeeker build
   ```

5. Deploy to S3:
   ```bash
   uv run zeeker deploy
   ```

## Project Structure

- `pyproject.toml` - Project dependencies and metadata
- `zeeker.toml` - Project configuration
- `resources/` - Python modules for data fetching
- `sglawwatch-zeeker.db` - Generated SQLite database (gitignored)
- `.venv/` - Virtual environment (gitignored)

## Dependencies

This project uses uv for dependency management. Common dependencies for data projects:

- `requests` - HTTP API calls
- `beautifulsoup4` - Web scraping and HTML parsing
- `pandas` - Data processing and analysis
- `lxml` - XML parsing
- `pdfplumber` - PDF text extraction
- `openpyxl` - Excel file reading

Add dependencies with: `uv add package_name`

## Resources

