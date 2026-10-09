# Universal Orlando Wait Time Tracker

Source: Queue-Times.com public API (free; display "Powered by Queue-Times.com").

## Setup
1. Create a free Neon Postgres DB; run `schema.sql` in its SQL Editor.
2. Push this folder to a **public** GitHub repo (free Actions minutes).
3. Repo > Settings > Secrets > Actions: add `DATABASE_URL` (use the pooled/SSL connection string).
4. Actions tab > collect-wait-times > Run workflow (smoke test). Check rows appear.
5. Deploy `dashboard.py` on Streamlit Community Cloud; add `DATABASE_URL` to its secrets.

## Local test
    export DATABASE_URL=postgresql://...
    FORCE=1 python collector.py

## Park IDs
65 Universal Studios Florida, 64 Islands of Adventure, 334 Epic Universe, 67 Volcano Bay

## Generating code documentation
Source files use Doxygen-style comments. With Doxygen installed: `doxygen Doxyfile`, then open `docs/html/index.html`.

## Repo layout
    .github/workflows/collect.yml   scheduled collector (GitHub Actions)
    collector.py                    fetch + store wait times
    dashboard.py                    Streamlit dashboard
    schema.sql                      Postgres tables (run once)
    tests/                          offline unit tests (python -m pytest)
