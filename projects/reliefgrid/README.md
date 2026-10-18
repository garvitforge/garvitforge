# ReliefGrid

**Emergency resource coordination dashboard — portfolio prototype.** ReliefGrid demonstrates an end-to-end workflow for submitting incident reports, triaging a queue, and viewing illustrative supply levels.

## What works

- Responsive dashboard with active, critical, available-unit, and resolved summaries.
- Report submission with client- and server-side validation, a generated report code, and immediate queue refresh.
- Persistent SQLite storage for incident reports; reports survive a server restart.
- Search and filters for status and priority; change priority, resolve, and reopen reports.
- Resource-readiness view with available/capacity indicators.
- JSON API, health endpoint, bounded request bodies, parameterized SQL, security headers, and safe text rendering.
- Automated standard-library integration tests for the API and core workflows.

## Run locally

Requires Python 3.10 or newer; there are no third-party dependencies.

```bash
cd projects/reliefgrid
python app.py
```

Open <http://127.0.0.1:5000>. The default database is `reliefgrid.db` in this directory. Configure `HOST`, `PORT`, or `RELIEFGRID_DB` through environment variables.

Run the tests:

```bash
python -m unittest discover -s tests -v
```

## API

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/health` | Service health |
| `GET` | `/api/dashboard` | Summary counts |
| `GET` | `/api/incidents` | List/filter reports (`q`, `status`, `severity`, `limit`, `offset`) |
| `POST` | `/api/incidents` | Validate and persist a report |
| `PATCH` | `/api/incidents/{id}` | Update report `status` or `severity` |
| `GET` | `/api/resources` | View illustrative inventory |

Example report body:

```json
{
  "kind": "Flooding",
  "description": "Water is blocking the east access road.",
  "district": "East District",
  "severity": "high"
}
```

## Deploying

`render.yaml` is a deployment blueprint for a Python web service with a persistent disk and `/api/health` check. A blueprint file does not create a live deployment by itself: connect the repository, review the service and disk settings, and deploy it on your host. No live public URL is claimed.

## Safety and limits

- Seed incidents and inventory are fictional demonstration data.
- This app is **not** connected to emergency services, dispatch, or live feeds. Do not use it for real-world emergencies or enter sensitive personal information.
- There is no authentication, authorization, spam protection, operational monitoring, or production security review. The demo is suitable for local review and portfolio evaluation, not real incident coordination.
- Inventory is illustrative and read-only in this prototype. It should not be treated as live availability.
