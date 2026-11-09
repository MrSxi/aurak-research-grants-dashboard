# AURAK Research Grants Dashboard

A web dashboard where AURAK students and faculty find UAE and international research grants, fellowships and scholarships in one place. Each grant shows its deadline, amount, eligibility, required documents and an Apply link, together with the AURAK faculty (all three schools) and students whose research matches it, and the evidence behind each match.

Software Engineering project, American University of Ras Al Khaimah. Supervisor: Prof. Khouloud Salameh.

## Architecture

| Part | Technology | Address | Role |
|---|---|---|---|
| Web frontend | ASP.NET Core 9, Razor views (`web-aspnet/`) | http://localhost:8000 | Renders every page, serves static files, health check at `/aspnet/health`, forwards form posts and the assistant API to the backend |
| Backend service | Python 3, FastAPI (`app/`) | http://127.0.0.1:8100 (local only) | Business logic, page data, grant collection, matching, research assistant, scheduled refresh |
| Database | Microsoft SQL Server Express (database `aurak`) | local instance `SQLEXPRESS` | All data. SQLite is supported as a fallback |
| Research assistant | Retrieval-augmented generation over the database | | Default model: Qwen 2.5 7B through Ollama. Claude (Anthropic or Microsoft Foundry) and Azure OpenAI can be switched on in `.env` |

How a page is served: the browser requests a page from ASP.NET. ASP.NET asks the backend for that page's data (`X-Render-As: json`), receives a JSON view-model (`app/web/viewmodel.py`) and renders it with the matching Razor view, for example `grants.html` with `Views/Pages/grants.cshtml`. Responses carry the header `X-Rendered-By: ASP.NET Razor (...)`. The Jinja templates in `app/templates` remain the reference design and are used when the backend is opened directly.

## Requirements

- Windows 10 or later
- Python 3.12+ and the .NET 9 SDK
- Microsoft SQL Server 2022 Express and ODBC Driver 18 for SQL Server
- Ollama (optional, for the research assistant)

## Setup

```powershell
python -m venv .venv-local
.venv-local\Scripts\python -m pip install --upgrade pip
.venv-local\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv-local\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

Load the faculty dataset into a local database (about 10 to 15 minutes the first time). The dataset ZIP is provided separately and is not stored in this repository:

```powershell
.venv-local\Scripts\python scripts\import_data.py --zip AURAK_combined_enriched_2026-10-14.zip
```

This imports the faculty records, links them to OpenAlex, runs every grant source, builds the embeddings and computes the matches. Running it again updates records instead of duplicating them.

To move the data into SQL Server (the database is created if it does not exist):

```powershell
.venv-local\Scripts\python scripts\migrate_to_mssql.py "mssql+pyodbc://@localhost\SQLEXPRESS/aurak?driver=ODBC+Driver+18+for+SQL+Server&trusted_connection=yes&TrustServerCertificate=yes"
```

Then put the same URL in `.env` as `DATABASE_URL=...`.

For the research assistant:

```powershell
ollama pull qwen2.5:7b
```

## Running

Double-click **Start Dashboard.bat**. It starts Ollama, the backend and the ASP.NET frontend, then opens http://localhost:8000. **Stop Dashboard.bat** stops everything. **Start Demo (reset demo student).bat** resets the demo student to "not approved" before starting.

Manual start, in two terminals:

```powershell
.venv-local\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8100
dotnet run --project web-aspnet -c Release --no-launch-profile
```

## Demo accounts (local prototype only)

| Role | Login | Password |
|---|---|---|
| Admin | admin@demo.aurak.local | admin123 |
| Faculty (linked to Dr. Khaled Abdalgader's record) | faculty@demo.aurak.local | faculty123 |
| Student, approved (Research Assistant) | student ID from `.env.example` | see `.env.example` |
| Student, not yet approved | student@demo.aurak.local | student123 |

Change these before the dashboard is shared beyond a local machine.

## Roles

- **Student:** signs up with name, AURAK email, student ID and research interests. Until an administrator approves dashboard access, a student only sees their profile and a "Request full access" button. Approved students browse grants, researchers and clusters, submit publications by DOI or OpenAlex ID for approval, and use the assistant.
- **Faculty:** profile built from the dataset and OpenAlex (interests, topics, publications, past grants, Scholar, Scopus and ORCID links). Faculty add or remove their own research interests without approval and get notifications when a new grant matches their research.
- **Admin:** approves or denies access requests, interests and publications (one at a time or in bulk). Approved items go straight into the database and the matching. Admins can add publications, remove faculty interests, edit or hide grants, check the health of every grant source and browse the database read-only.

## Grant sources

All grants are collected automatically. Nothing is entered by hand.

| Source | Method |
|---|---|
| Grants.gov | Public REST API |
| EU Funding & Tenders Portal | Public search API |
| Dubai RDI | Public WordPress API of dubairdi.ae |
| Al Qasimi Foundation (Ras Al Khaimah) | Page scraper |
| Al Jalila Foundation | Page scraper |
| Sandooq Al Watan, ATRC/ASPIRE, ECA, ERDC, DoH Abu Dhabi, MOHAP | Checked every refresh (no call listings published at the moment) |

The refresh runs every 6 hours (`REFRESH_HOURS`) and can be started from the admin pages. Collectors respect each site's robots.txt, make one request per page per refresh, link to the funder's own page and store a date or amount only when it is printed on the page. A failing source keeps its last good data.

## Matching

`app/services/matching.py`. Each person is represented by evidence items: research interests, OpenAlex topics, keywords, publications (title and abstract) and past grants or projects, embedded locally with `all-MiniLM-L6-v2`. A grant is compared with every person's evidence, and the components are combined with configurable weights (`MATCH_WEIGHT_*` in `.env`) into a match score. Matches below `MATCH_MIN` are not shown. Faculty are listed before students, and every match lists the evidence behind it.

## Research clusters

AURAK has no official research clusters, so the project proposes ten (`app/clusters.py`), drawn from the departments and interests in the dataset. Membership is computed from each person's evidence and labelled as AI-suggested throughout the interface.

## Data rules

Nothing is invented: missing values show as "Not published", and every figure on screen comes from the database. Scores are match scores, not probabilities, and a match does not mean a person is available to collaborate.

## Project layout

```
app/                    Python backend
  main.py               application, scheduler, error pages
  config.py             settings (read from .env)
  models.py             database schema
  connectors/           grant sources (grants_gov, eu_ft, uae)
  services/             matching, refresh, research assistant (chat, llm), clusters
  web/                  routes and page view-models
  templates/, static/   reference templates and the AURAK stylesheet
web-aspnet/             ASP.NET Core frontend
  Program.cs            hosting, static files, routing to the backend
  Infrastructure/       page controller, view-model helpers, HTML encoder
  Views/Pages/          one Razor view per page
  Views/Shared/         layout and shared parts
scripts/                import, SQL Server migration, demo reset, OpenAlex check
```
