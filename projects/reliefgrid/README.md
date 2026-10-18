# ReliefGrid

**Emergency resource coordination dashboard — portfolio prototype.** ReliefGrid demonstrates an end-to-end workflow for submitting incident reports, triaging a queue, and viewing illustrative supply levels.

## What works

- Responsive dashboard with active, critical, available-unit, and resolved summaries.
- Report submission with client- and server-side validation, a generated report code, and immediate queue refresh.
- Persistent SQLite storage for incident reports; reports survive a server restart.
- Search and filters for status and priority; change priority, resolve, and reopen reports.
- Optional Twilio escalation: a new critical incident sends SMS to configured responders and schedules a voice call two minutes later unless someone acknowledges it.
- Persisted notification jobs survive restarts; alert delivery is disabled unless explicitly enabled with server-side settings.
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

## SMS and call escalation

ReliefGrid uses Twilio's REST API for opt-in outbound alerts. Configure these values in the hosting environment (or your local shell), never in source control:

| Variable | Purpose |
|---|---|
| `RELIEFGRID_ALERTS_ENABLED` | Set to `true` to enable delivery; otherwise alerts remain queued and no network requests are made. |
| `TWILIO_ACCOUNT_SID` | Twilio Account SID. |
| `TWILIO_AUTH_TOKEN` | Twilio Auth Token; treat as a secret. |
| `TWILIO_FROM_NUMBER` | Twilio phone number in E.164 format, such as `+14155550123`. |
| `RELIEFGRID_ALERT_RECIPIENTS` | Comma-separated responder phone numbers in E.164 format. |

When a critical incident is reported (or an active incident is raised to critical), the dispatcher sends an SMS and schedules a voice call for 120 seconds later. The **Acknowledge** action cancels a pending voice call; the SMS still goes out. Calls already submitted to Twilio cannot be recalled. Jobs are stored in SQLite and processed by a background thread in the web process. Failed attempts are recorded and are not automatically retried, to avoid duplicate or unexpected calls.

The app accepts incident reports without authentication. Keep outbound alerts disabled on any publicly reachable demo until the app has suitable authentication, rate limiting, and operational safeguards. Use opted-in test recipients first; Twilio usage may incur charges and messaging/call rules vary by region. This prototype is not an emergency dispatch system.

## API

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/health` | Service health |
| `GET` | `/api/dashboard` | Summary counts |
| `GET` | `/api/incidents` | List/filter reports (`q`, `status`, `severity`, `limit`, `offset`) |
| `POST` | `/api/incidents` | Validate and persist a report |
| `PATCH` | `/api/incidents/{id}` | Update report `status`, `severity`, or acknowledge (`{"acknowledge": true}`) |
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
- SMS and calls are optional integrations that require a configured Twilio account and explicit opt-in; enabling them on an unauthenticated public instance can incur costs or expose recipients to abuse.
- Inventory is illustrative and read-only in this prototype. It should not be treated as live availability.
