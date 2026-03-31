# API Refactor Baseline

## Scope

This baseline captures route and contract checkpoints before splitting `api/main.py`.
It is focused on the three high-change domains:

- `/notifications/*`
- `/setup/*`
- `/auto-trader/*`

## Route Inventory Snapshot

From `api/main.py`:

- Total routes: ~79
- `/notifications/*`: 3
- `/setup/*`: 8
- `/auto-trader/*`: 34

## Domain Route Lists

### Notifications

- `GET /notifications/status`
- `GET /notifications/preferences`
- `PUT /notifications/preferences`

### Setup

- `GET /setup/config`
- `POST /setup/config`
- `POST /setup/risk-config`
- `GET /setup/services/status`
- `GET /setup/longport/diagnostics`
- `POST /setup/services/start`
- `POST /setup/services/stop`
- `POST /setup/services/stop-all`

### Auto Trader

- `GET /auto-trader/strong-stocks`
- `GET /auto-trader/strategy-score`
- `GET /auto-trader/strategies`
- `GET /auto-trader/pair-backtest`
- `POST /auto-trader/scan/run`
- `GET /auto-trader/signals`
- `POST /auto-trader/signals/{signal_id}/confirm`
- `POST /auto-trader/config`
- `GET /auto-trader/status`
- `GET /auto-trader/metrics/recent`
- `GET /auto-trader/metrics/sla`
- `GET /auto-trader/research/status`
- `GET /auto-trader/research/snapshot`
- `POST /auto-trader/research/run`
- `GET /auto-trader/research/tasks/{task_id}`
- `POST /auto-trader/research/tasks/{task_id}/cancel`
- `GET /auto-trader/research/model-compare`
- `POST /auto-trader/research/strategy-matrix/run`
- `GET /auto-trader/research/strategy-matrix/result`
- `POST /auto-trader/research/ml-matrix/run`
- `GET /auto-trader/research/ml-matrix/result`
- `POST /auto-trader/research/ml-matrix/apply-to-config`
- `GET /auto-trader/research/ab-report`
- `GET /auto-trader/research/ab-report/markdown`
- `GET /auto-trader/templates`
- `GET /auto-trader/config/policy`
- `POST /auto-trader/config/agent`
- `POST /auto-trader/template/apply`
- `GET /auto-trader/template/preview`
- `GET /auto-trader/config/export`
- `POST /auto-trader/config/import`
- `GET /auto-trader/config/backups`
- `POST /auto-trader/config/rollback`
- `GET /auto-trader/config/rollback/preview`

## Contract Freeze Checklist (Do-Not-Break)

### HTTP Contract

- Keep method + path unchanged for all listed routes.
- Keep response field names unchanged.
- Keep existing default values and nullability behavior.
- Keep error shape and status codes consistent.

### MCP Compatibility

- Preserve existing API paths used by MCP-side API proxy callers.
- Preserve Python import compatibility for modules referenced directly by MCP scripts, especially:
  - `api.notification_preferences`

### Frontend Compatibility

- Preserve fields consumed by:
  - `frontend/app/notifications/page.tsx`
  - `frontend/app/setup/page.tsx`
  - `frontend/app/auto-trader/page.tsx`

## High-Risk Areas

- Setup process control and PID orchestration.
- Auto-trader research async task queue and metrics events (`api.auto_trader.*`).
- Worker/supervisor sync and status composition in `/auto-trader/status` and `/setup/services/status`.

