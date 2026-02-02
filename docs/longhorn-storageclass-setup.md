### Factory-Edge on Talos and Longhorn StorageClass

This doc captures exactly what went wrong and the steps used to fix it.

---

#### 0) Symptoms

#### Longhorn install symptoms
- `longhorn-manager` **CrashLoopBackOff**
- `longhorn-driver-deployer` stuck in `Init:0/1` with logs showing **"waiting"**
- `kubectl get storageclass` returned **No resources found**

#### App symptoms (Factory-Edge)
- `factory-edge-db-0` (Timescale/Postgres) **CrashLoopBackOff**
- `factory-edge-connector` **CrashLoopBackOff**
- Connector error earlier:
  - `psycopg2.OperationalError ... Operation not permitted`
- PVC existed and was bound, but DB refused to initialize

---

#### 1) Root causes

#### 1.1 Longhorn was blocked by Kubernetes Pod Security Admission (PSA)
Kubernetes events showed Longhorn pods were forbidden under `baseline` policy because Longhorn requires:
- `privileged` container
- `hostPath` mounts

#### 1.2 Talos worker was missing required host tooling for Longhorn
Even after PSA was fixed, Talos worker lacked host tools used by Longhorn (notably iSCSI + util-linux).
Longhorn components stayed unhealthy until I added these.

#### 1.3 Postgres/Timescale failed due to `lost+found` on the PVC mountpoint
Timescale logs:
- `initdb: error: directory "/var/lib/postgresql/data" exists but is not empty`
- `It contains a lost+found directory...`
- Fix: use a **subdirectory** inside the mount, not the mount root.

Also: Helm rendered the fix, but the StatefulSet pod was still running the *old revision* until I forced a re-create.

---

#### 2) Fix Longhorn on Talos (PSA + Talos Image Factory extensions)

#### 2.1 Observe the PSA block
```bash
kubectl -n longhorn-system get events --sort-by=.lastTimestamp | tail -n 30
```

You see errors like:

- violates PodSecurity "baseline:latest"

- hostPath volumes, privileged container not allowed

#### 2.2 Allow privileged for Longhorn namespace
```shell
kubectl label ns longhorn-system \
  pod-security.kubernetes.io/enforce=privileged \
  pod-security.kubernetes.io/enforce-version=latest \
  pod-security.kubernetes.io/audit=privileged \
  pod-security.kubernetes.io/warn=privileged \
  --overwrite
```

#### 2.3 Build a Talos Image Factory schematic with required extensions
- [talos-factory-image](https://factory.talos.dev/)

- In `factory.talos.dev`, I created a schematic for Talos `v1.12.0` including:

  - `siderolabs/iscsi-tools`
  - `siderolabs/util-linux-tools`

- Schematic ID used:

```shell
613e1592b2da41ae5e265e8789429f22e121aab91cb4deb6bc3c0b6262961245
```

#### 2.4 Upgrade the worker to the schematic installer image
```shell
WORKER=192.168.0.244
SCHEM=613e1592b2da41ae5e265e8789429f22e121aab91cb4deb6bc3c0b6262961245

talosctl -n $WORKER upgrade --image factory.talos.dev/installer/${SCHEM}:v1.12.0
```


#### 2.5 Restart Longhorn components and confirm health
```shell
kubectl -n longhorn-system delete pod -l app=longhorn-manager
kubectl -n longhorn-system delete pod -l app=longhorn-driver-deployer

kubectl -n longhorn-system get pods -o wide
kubectl get storageclass
```


- Expected results:
  - `longhorn-manager` > `2/2 Running`
  - CSI pods `Running`
  - StorageClass created:
    - `longhorn (default)`
    - `longhorn-static`

#### 3) Fix Factory-Edge DB CrashLoop (Timescale/Postgres on Longhorn)
#### 3.1 Identify the DB init failure
```shell
kubectl -n factory-edge logs factory-edge-db-0 -c timescaledb --tail=200
```


- Key error:
  - `lost+found` exists in `/var/lib/postgresql/data`
  - Postgres refuses to initdb on a non-empty mount root

#### 3.2 Apply the correct fix: set PGDATA to a subdirectory

- In the DB StatefulSet, add:

```shell
- name: PGDATA
  value: /var/lib/postgresql/data/pgdata
```


- So Postgres initializes under:
- `/var/lib/postgresql/data/pgdata`
- and ignores lost+found at the mount root

#### 3.3 Deploy via Helm and force the StatefulSet to roll to the new revision

- I deploy with:

```shell
helm upgrade --install factory-edge charts/factory-edge \
  -n factory-edge \
  -f charts/factory-edge/values.yaml
```


- Helm showed the manifest contained PGDATA, but the running pod stayed on the old revision.

- I confirmed revision mismatch:

```shell
kubectl -n factory-edge get sts factory-edge-db -o jsonpath='{.spec.updateStrategy.type}{"\n"}{.status.currentRevision}{"\n"}{.status.updateRevision}{"\n"}'
```

- Then forced recreation:

```shell
kubectl -n factory-edge delete pod factory-edge-db-0
kubectl -n factory-edge get pod factory-edge-db-0 -w
```


- Result:
  - `factory-edge-db-0` came up `1/1 Running` with 0 restarts

#### 4) Verify DB connectivity from the connector

- I verified the DB port is reachable from the connector pod:

```shell
kubectl -n factory-edge exec deploy/factory-edge-connector -- \
  bash -c 'timeout 2 bash -c "</dev/tcp/factory-edge-db/5432" && echo "Port is OPEN" || echo "Port is CLOSED"'

```

- Output:
  - `Port is OPEN`

- So network + service discovery is correct.

#### 5) Connector behavior after DB fix

- After DB became healthy, connector logs stopped showing psycopg2 connection errors.
- The only output observed was a non-fatal warning:

```shell
kubectl -n factory-edge logs deploy/factory-edge-connector --tail=120
```


- Example:
  - DeprecationWarning: Callback API version 1 is deprecated

- This indicates the connector is starting and running (the warning is not a crash).

#### 6) Summary: What I solved

- Longhorn on Talos:
  - Fixed PSA policy preventing privileged/hostPath workloads 
  - Added required Talos system extensions via Image Factory schematic + upgrade 
  - Longhorn pods all healthy 
  - StorageClass longhorn created and default

- Postgres/Timescale on Longhorn:
  - Fixed initdb failure caused by lost+found in PVC mount root 
  - Used PGDATA subdirectory pattern 
  - Forced StatefulSet to roll to the new template revision 
  - DB pod became stable (Running)

- Connector:
  - Confirmed DB port is open from connector namespace/pod 
  - Connector no longer blocked by DB being down

#### 7) “Worker node” rule

- For any new Talos worker that should run Longhorn volumes:
  - Install/upgrade Talos using the same Image Factory schematic (iscsi + util-linux).
  Otherwise Longhorn may fail on that node.
