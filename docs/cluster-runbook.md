### Factory Edge Lab on Talos Kubernetes — Helm + Observability Runbook (Drop-in)

This doc captures everything from a Kubernetes + Helm + Observability perspective:
- Deploy Factory Edge (MQTT → Connector → Timescale historian) on Talos
- Add Longhorn for dynamic PVCs
- Integrate with existing kube-prometheus-stack (ServiceMonitor + Grafana dashboards)
- Fix common Helm/Grafana provisioning pitfalls

---

### 0) Prereqs

- Talos Kubernetes cluster (working `kubectl`)
- Existing monitoring stack (kube-prometheus-stack) in `monitoring` namespace:
  - Prometheus + Grafana running
  - ServiceMonitor CRD exists

Verify:

```bash
kubectl -n monitoring get pods | egrep -i 'prometheus|grafana|operator|alertmanager'
kubectl get crd | grep -i servicemonitor
```

- Expected:
  - `Grafana pod `kps-grafana...` is Running 
  - Prometheus `prometheus-kps-...` is Running
  - `servicemonitors.monitoring.coreos.com` exists

### 1) Storage on Talos: Install Longhorn (recommended)

- Why: Talos clusters often start with no StorageClass, so StatefulSets (TimescaleDB) need dynamic PVCs.

### 1.1 Install Longhorn (example)

- Install via Helm or Longhorn manifest

### 1.2 Validate StorageClass exists
```shell
kubectl get storageclass
```


- Expected:
  - longhorn (default) exists

### 1.3 Validate Longhorn nodes are Ready
```shell
kubectl -n longhorn-system get nodes.longhorn.io
```

### 2) Build & Push Images

- I used images pulled from personal Docker Hub:

- Example values:

```yaml
images:
  mosquitto: eclipse-mosquitto:2
  timescaledb: timescale/timescaledb:latest-pg16
  connector: docker.io/anselemo/factory-edge-connector:0.1.0
  simulator: docker.io/anselemo/factory-edge-simulator:0.1.0
```


> Recommended: build in CI → push tags → Helm pulls by tag.

### 3) Helm Chart Structure (what we used)

- Chart root:

```yaml
charts/factory-edge/
  Chart.yaml
  values.yaml
  templates/
    _helpers.tpl
    namespace.yaml (optional; we created NS manually)
    deployment-connector.yaml
    deployment-simulator.yaml
    deployment-mosquitto.yaml
    service-connector.yaml
    service-mosquitto.yaml
    statefulset-timescaledb.yaml
    service-timescaledb.yaml
    pvc-timescaledb.yaml (deleted; replaced by StatefulSet volumeClaimTemplates)
    servicemonitor-connector.yaml
    configmap-grafana-dashboard.yaml
  dashboards/
    factory-edge-dashboard.json
```


- Key rule:

  - Only Kubernetes manifests belong in templates/ 
  - JSON dashboards belong in dashboards/ (NOT templates/)

### 4) Namespace + Install
### 4.1 Create namespace (explicit)

- I created it manually to avoid namespace timing issues:

```shell
kubectl create namespace factory-edge
```

### 4.2 Helm install/upgrade
```shell
helm upgrade --install factory-edge charts/factory-edge \
  -n factory-edge \
  -f charts/factory-edge/values.yaml
```

### 4.3 Validate workloads
```shell
kubectl -n factory-edge get pods
kubectl -n factory-edge get svc
```


- Expected:
  - `factory-edge-mqtt` Running
  - `factory-edge-db-0` Running
  - `factory-edge-connector` Running
  - `factory-edge-simulator` Running (if enabled)

### 5) Observability Integration (kube-prometheus-stack)
### 5.1 ServiceMonitor for Prometheus scraping

- I created a ServiceMonitor in `monitoring` so Prometheus discovers metrics `from factory-edge`.

- Verify:

```shell
kubectl -n monitoring get servicemonitor | grep -i factory
kubectl -n monitoring get servicemonitor factory-edge-connector -o yaml | sed -n '1,200p'
```
### 5.2 Confirm Prometheus is scraping the connector

### Option A: Prometheus UI (port-forward)

```shell
kubectl -n monitoring port-forward svc/kps-kube-prometheus-stack-prometheus 9090:9090
```

- Open:` http://localhost:9090`

- Run:

```shell
up{namespace="factory-edge"}
```


- should see:

```shell
job="factory-edge-connector" → 1
```

- Confirm metrics exist:

```shell
connector_last_message_unix{job="factory-edge-connector"}
```

### 6) Grafana Dashboard Provisioning (ConfigMap sidecar)
### 6.1 How kube-prometheus-stack imports dashboards

- Grafana has sidecar containers:
  - grafana-sc-dashboard (watches ConfigMaps with a label)
  - mounts dashboards to /tmp/dashboards

- deployment confirmed:
  - LABEL=grafana_dashboard

- So ConfigMap must have:

```yaml
metadata:
  labels:
    grafana_dashboard: "1"
```


- Verify sidecar label:

```shell
kubectl -n monitoring get deploy kps-grafana -o yaml | grep -n "LABEL" -n | head -n 30
```
### 6.2 Dashboard ConfigMap template (important)

- used this template:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: factory-edge-dashboard
  namespace: {{ .Values.grafana.dashboard.namespace }}
  labels:
    {{ .Values.grafana.dashboard.labelKey }}: {{ .Values.grafana.dashboard.labelValue | quote }}
data:
  factory-edge-dashboard.json: |-
{{ .Files.Get "dashboards/factory-edge-dashboard.json" | indent 4 }}
```

### 6.3 The 2 provisioning bugs fixed
- Bug #1: Dashboard was empty in ConfigMap

- Symptom:

```yaml
data:
  factory-edge-dashboard.json: ""
```


- Root cause:
  - Folder typo: dashbaords/ instead of dashboards/

- Fix:

```shell
mv charts/factory-edge/dashbaords charts/factory-edge/dashboards
```

- Confirm render:

```shell
helm template factory-edge charts/factory-edge | grep -n "factory-edge-dashboard.json" -A5
```
- Bug #2: Helm upgrade failed with “apiVersion not set, kind not set”

- Symptom:

```shell
UPGRADE FAILED: error validating data: [apiVersion not set, kind not set]
```

- Root cause:
  - A values fragment file was incorrectly placed under `templates/`:
    - `templates/grafana-persistance-values.yaml`

- Fix:

```shell
rm charts/factory-edge/templates/grafana-persistance-values.yaml
# or move it out of templates/
```

### 7) Dashboard “No data” after import (important)
### 7.1 Why it happened

> Prometheus scraping was OK, but the dashboard JSON contained a hardcoded datasource UID
> from another Grafana instance (e.g., your docker-compose Grafana).
> So the dashboard loaded, but panels queried a datasource that doesn’t exist → No data.

### 7.2 Correct job label

- Prometheus showed the connector scrape job as:
  - job="factory-edge-connector"

- So metric queries should filter like:

```shell
...{job="factory-edge-connector"}
```


- Example:

```shell
time() - connector_last_message_unix{job="factory-edge-connector"}
```
### 7.3 Fix datasource UID (cluster Grafana)

- Get Grafana admin creds:

```shell
USER=$(kubectl -n monitoring get secret kps-grafana -o jsonpath='{.data.admin-user}' | base64 -d)
PASS=$(kubectl -n monitoring get secret kps-grafana -o jsonpath='{.data.admin-password}' | base64 -d)
```

- Port-forward:

```shell
kubectl -n monitoring port-forward svc/kps-grafana 3000:80
```

- Verify API:

```shell
curl -i -u "$USER:$PASS" http://localhost:3000/api/health | head -n 20
```
- List datasources (raw first):

```shell
curl -s -u "$USER:$PASS" http://localhost:3000/api/datasources | head -n 80
```
- If it returns JSON array, parse with jq:

```shell
curl -s -u "$USER:$PASS" http://localhost:3000/api/datasources \
| jq -r '.[] | "\(.name) type=\(.type) uid=\(.uid)"'
```
>  - Then update the dashboard JSON to use the current Prometheus UID (preferred: use a datasource variable like `${DS_PROMETHEUS}` in the JSON).

- After updating JSON:

```shell
helm upgrade --install factory-edge charts/factory-edge -n factory-edge -f charts/factory-edge/values.yaml
kubectl -n monitoring rollout restart deploy/kps-grafana
```
### 8) Factory Edge Dashboard Panels (PromQL + SQL)
- Panel 1 — Ingest rate (per topic)
```shell
sum by (topic) (rate(mqtt_messages_total{job="factory-edge-connector"}[1m]))
```

- Panel 2 — Connector errors
```shell
increase(connector_errors_total{job="factory-edge-connector"}[5m])
```

- Panel 3 — Last message age (seconds)
```shell
time() - connector_last_message_unix{job="factory-edge-connector"}
```

- Panel 4 — Latest events table (TimescaleDB)

- SQL (requires Postgres datasource in cluster Grafana):

```shell
SELECT ts, topic, value
FROM measurements
ORDER BY ts DESC
LIMIT 20;
```


> Note: kube-prometheus-stack Grafana will not automatically have a Postgres datasource unless you provision it. Until then, the table panel will show No data.

### 9) Quick Troubleshooting Checklist
- Pods not Running
```shell
kubectl -n factory-edge get pods
kubectl -n factory-edge describe pod <pod>
kubectl -n factory-edge logs <pod> --previous --tail 200
```
- Prometheus not scraping
```shell
kubectl -n monitoring get servicemonitor | grep -i factory
kubectl -n factory-edge get svc factory-edge-connector -o yaml | sed -n '1,200p'
kubectl -n factory-edge get endpoints factory-edge-connector -o yaml | sed -n '1,200p'
```
- Dashboard not appearing
```shell
kubectl -n monitoring get cm | grep -i factory-edge-dashboard
kubectl -n monitoring logs deploy/kps-grafana -c grafana-sc-dashboard --tail 120
kubectl -n monitoring rollout restart deploy/kps-grafana
```
- Dashboard appears but No data 
  - Prometheus check:

```shell
up{namespace="factory-edge"}
connector_last_message_unix{job="factory-edge-connector"}
```
- Fix datasource UID mismatch in JSON

### 10) Next Steps (recommended order)

- Expose Grafana via Ingress 
  - install ingress-nginx 
  - create Ingress for kps-grafana

- Add MQTT auth / TLS 
  - username/password first 
  - mTLS later (production-like)

- Add alerts 
  - message age too high 
  - error counter increase 
  - ingest rate drop to zero

- Second connector example 
  - MQTT → HTTP API 
  - MQTT → Kafka (optional)

- GitOps 
  - Store charts + dashboards + runbooks in repo 
  - CI builds images → pushes tags → helm upgrade