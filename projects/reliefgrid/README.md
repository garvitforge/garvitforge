# ReliefGrid

**Emergency resource coordination dashboard** — a portfolio prototype demonstrating report intake, incident triage, SQLite persistence, and resource visibility.

## Features
- Responsive operations dashboard and priority queue.
- **Working report submission:** form validation, POST API, generated report ID, SQLite persistence, and immediate queue refresh.
- Resolve incidents and update dashboard metrics.
- Resource readiness cards seeded with clearly illustrative sample inventory.
- JSON API endpoints and a health check.

## Run locally
Python 3.10+; no third-party packages required.

```bash
python app.py
```
Open http://127.0.0.1:5000.

## API
- GET /api/health
- GET /api/dashboard
- GET /api/incidents
- POST /api/incidents
- PATCH /api/incidents/<id>
- GET /api/resources

## Deployment
This is a small single-file deployable edition. For a public hosted version, deploy the repository/subdirectory to a Python host with a persistent writable disk. Set `HOST=0.0.0.0`, use the platform-provided `PORT`, and set `RELIEFGRID_DB` to a file on the persistent disk. A repository upload does **not** itself create a running website; the host must be configured and deployed.

## Safety and honesty
All initial incidents and inventory are fictional sample data. This is not connected to emergency services or verified live feeds. Do not enter sensitive personal information or use it for real-world emergency coordination. No authentication, spam protection, or production security review is included.
