# Operating Concept — Factory Edge Lab

## Purpose
Operate a simple factory edge data pipeline:
UNS telemetry over MQTT → connector ingestion → historian storage → observability.

## Components
- Mosquitto (MQTT broker): receives UNS topics
- Simulator: publishes sample UNS telemetry (represents edge devices)
- Connector: subscribes to `uns/#`, validates/ingests, writes to historian; exposes Prometheus metrics
- TimescaleDB (historian): durable time-series/event storage
- Prometheus/Grafana: monitoring + dashboards

## Deployment model
- Single-node (docker compose) for development/pilot
- Intended rollout: repeatable “site package” with consistent config (site/line/cell identifiers)

## Security baseline (current + direction)
Current: local demo, no auth/TLS.
Direction for production:
- MQTT auth (username/password) and/or mTLS
- Secrets via environment variables or Docker/K8s secrets (no plaintext in repo)
- Network segmentation: broker not exposed publicly; allow only connectors/devices
- Least privilege DB user for inserts/selects only

## Monitoring signals (what to watch)
Primary signals:
- Ingest rate per topic: `sum by (topic) (rate(mqtt_messages_total[1m]))`
- Pipeline staleness: `time() - connector_last_message_unix`
- Ingestion errors: `increase(connector_errors_total[5m])`

Recommended thresholds:
- Staleness warning: > 10s
- Staleness critical: > 30s
- Errors: > 0 in 5m

## Incident handling (quick runbook)
### Symptom: ingest rate drops to zero
Checks:
1) `docker ps` (simulator/connector up?)
2) MQTT flow:
   `mosquitto_sub -t 'uns/#' -v -C 5`
3) Connector metrics reachable: `/metrics`
Actions:
- restart simulator, then connector
- validate broker health/logs

### Symptom: staleness rising (last message age increasing)
Checks:
- MQTT publishes still arriving?
- Connector logs for parsing/DB errors
Actions:
- restart connector
- if DB errors, validate DB container and disk

### Symptom: errors increase
Checks:
- connector logs for payload parsing or DB insert failures
Actions:
- quarantine bad topic/payload if needed
- validate DB connectivity and schema

## Data retention & backup (historian)
For production direction:
- retention policy per topic (e.g. raw 7–30 days, aggregates longer)
- backup via scheduled `pg_dump` + restore test procedure

## Rollout checklist (per site)
- Define site identifiers (site/line/cell/asset naming)
- Apply UNS topic conventions and ownership
- Configure broker auth + network policy
- Deploy connector with site-specific config
- Validate dashboards: ingest, errors, staleness, latest events
