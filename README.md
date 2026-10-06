# Uyamaa Fleet API

The backend the frontend needs, in one FastAPI service on the shared PostgreSQL database
(the same tables as the other services: users, dataCenter, hardDrive, smartReading,
prediction, maintenance, alert, replacement).

| Method | Path | Frontend insert point |
|---|---|---|
| GET | `/api/health` | health check |
| GET | `/api/dashboard?scope=` | 1 |
| GET | `/api/drives?q=&status=&scope=&page=&page_size=` | 2, 2b |
| GET | `/api/drives/{serial}` | 3 (404 if unknown) |
| GET | `/api/alerts?severity=&range=&scope=` | 4 |
| GET / POST | `/api/maintenance` | 5, 6 |
| GET / POST | `/api/replacements?scope=&month=YYYY-MM` | 7, 8 |
| GET | `/api/users` | 9 |
| GET | `/api/reports/summary?scope=&month=YYYY-MM` | 10 |

Interactive docs: `/docs`. Already deployed elsewhere: `/predict` (failure predictor) and `/api/ai/logs/{userid}`.

## Run

    pip install -r requirements.txt
    DATABASE_URL=postgresql://user:pass@host:5432/db uvicorn app.main:app --reload
    pytest -q          # uses a throw-away SQLite file, not your database

## How database values are interpreted

- `hardDrive.status` is free text: contains "crit"/"fail" -> critical, "warn"/"risk"/"degrad" -> warning, anything else -> healthy.
- `alert.severity`: "crit"/"high" -> critical, "warn"/"med" -> warning, otherwise info.
- An alert is **open** until `alert.outcome` is Resolved, Closed or Dismissed.
- `hardDrive.capacity` is reported as terabytes.

## Known gaps (the schema has no column for these)

- **Who performed a write**: there is no login. Writes are attributed to the `X-User-Id` header, else `DEFAULT_USER_ID`, else the first user.
- **Rack**: reported as `n/a`. **SMART capture time**: `smartReading` has no timestamp, so the response uses the time of the request.
- **Alert time**: `alert_date` is a date only, so the time of day shows as 00:00.
