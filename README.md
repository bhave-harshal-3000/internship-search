# DeafLink Scraper

DeafLink is a Python-based scraper suite focused on disability-friendly job and internship listings. It collects opportunities from targeted sources and writes one Excel file per source into the output directory.

## Features

- Sources focused on PWD and inclusive hiring
- Per-source Excel output with consistent columns
- Built-in negative-signal filtering (voice/telecalling, field sales, etc.)
- Deduplication by apply URL (fallback: title + company + location)

## Project Layout

- [main.py](main.py)
- [scrapers/](scrapers/)
- [utils/](utils/)
- [output/](output/)

## Install

1) Create and activate a virtual environment (recommended)
2) Install dependencies

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

```bash
python main.py
```

Outputs are written to [output/](output/) as one XLSX per source.

## Sources

The following scrapers are wired in [main.py](main.py):

- Naukri (PWD filter)
- NCS (National Career Service)
- Google Jobs (SerpAPI)
- Company disability/inclusive pages
- Atypical Advantage
- SwarajAbility

## Google Jobs (SerpAPI)

Set your SerpAPI key in an environment variable before running:

```bash
setx SERPAPI_KEY "your_key_here"
```

Alternatively, create a .env file at the project root with:

```
SERPAPI_KEY=your_key_here
```

## Output Columns

The schema is defined in [utils/schema.py](utils/schema.py). Newly added columns include:

- confidence_level
- backend_type

## Notes

- If a source site changes layout, update its scraper in [scrapers/](scrapers/).
- If you want to add or remove sources, edit [main.py](main.py).

# Intern-Search

Intern-Search is a Python-based scraper suite focused on disability-friendly job and internship listings. It collects opportunities from targeted sources and writes one Excel file per source into the output directory.

## Features

- Sources focused on PWD and inclusive hiring
- Per-source Excel output with consistent columns
- Built-in negative-signal filtering (voice/telecalling, field sales, etc.)
- Deduplication by apply URL (fallback: title + company + location)

## Project Layout

- [main.py](main.py)
- [scrapers/](scrapers/)
- [utils/](utils/)
- [output/](output/)

## Install

1) Create and activate a virtual environment (recommended)
2) Install dependencies

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

```bash
python main.py
```

Outputs are written to [output/](output/) as one XLSX per source.

## Streamlit UI

Run the Streamlit frontend:

```bash
streamlit run app.py
```

Select the sources you want to run and download the generated XLSX files from the page.

## Sources

The following scrapers are wired in [main.py](main.py):

- Naukri (PWD filter)
- NCS (National Career Service)
- Google Jobs (SerpAPI)
- Company disability/inclusive pages
- Atypical Advantage
- SwarajAbility

## Google Jobs (SerpAPI)

Set your SerpAPI key in an environment variable before running:

```bash
setx SERPAPI_KEY "your_key_here"
```

Alternatively, create a .env file at the project root with:

```
SERPAPI_KEY=your_key_here
```

## Output Columns

The schema is defined in [utils/schema.py](utils/schema.py). Newly added columns include:

- confidence_level
- backend_type

## Notes

- If a source site changes layout, update its scraper in [scrapers/](scrapers/).
- If you want to add or remove sources, edit [main.py](main.py).
