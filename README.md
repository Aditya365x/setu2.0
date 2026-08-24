# SETU

**Real-time disaster early-warning and resource coordination platform.**

> India's warning system already works. What breaks is the six hours after the warning.

Detection (IMD, CWC, INCOIS) and dissemination (NDMA SACHET) are mature national
capabilities. Coordination and allocation are not — a District Emergency
Operations Centre runs a live multi-agency operation on WhatsApp groups, phone
calls and a paper register. SETU is the missing allocation layer between the
alert and the boots on the ground.

Full design: [docs/SETU_Disaster_Platform_Plan (1).pdf](docs/SETU_Disaster_Platform_Plan%20(1).pdf) ·
build status: [PLAN.md](PLAN.md)

---

## Five minutes to a running system

```bash
docker compose up --build -d     # or: make up
make seed                        # Ganjam district, 62 shelters, 40 units
make demo                        # Cyclone Landfall: 200 reports over 90s
```

| Surface | URL |
|---|---|
| DEOC dashboard | http://localhost:5173 |
| Citizen PWA | http://localhost:5174 |
| API docs | http://localhost:8000/docs |

`make reset` returns to a clean, seeded state in about five seconds. Use it
between rehearsals — it is what makes ten run-throughs possible instead of three.

**This runs with the network unplugged.** `OFFLINE_MODE=true` is the default:
CAP alerts come from `fixtures/`, ETAs from the haversine router, and the map
draws no external tiles. Test it by actually turning the Wi-Fi off.

## Opening the PWA on a phone

**Use the HTTPS URL, not the HTTP one.** Geolocation, camera and service
workers are all gated behind a secure context. `localhost` is exempt — which is
why everything works on the demo laptop and then appears broken the moment a
phone opens `http://192.168.x.x:5174`. The permission is never even requested.

```bash
# Point the certificate at this machine's LAN address, then rebuild the PWA:
CERT_HOSTS=192.168.2.102 docker compose up -d --build pwa
```

Then open **`https://<LAN-IP>:5443`** on the phone and accept the one-time
certificate warning. The certificate is self-signed and generated inside the
container at startup, so this still works with the Wi-Fi unplugged.

**If GPS is unavailable anyway** — permission denied, no fix indoors, a borrowed
phone — the app never dead-ends. It says which of those happened and offers a
six-digit PIN code instead, resolved against the same table the SMS channel
uses. That report is stored at `gps_accuracy_m = 3000`, which automatically
lowers its trust score and widens its clustering radius, and the operator sees
an uncertainty circle rather than a false-precision pin. A report with a 3 km
circle is worth far more than no report.

---

## What it does

```
INGEST → CLUSTER → SCORE → OPTIMIZE → DISPATCH → RESOLVE
                                                     ↓
                        freed capacity re-triggers optimisation
```

Four ingest channels degrade gracefully — PWA online, PWA offline, SMS, IVR —
and every one produces the same normalised report object. The optimizer cannot
tell which channel a report arrived on; it sees only location, hazard, severity
and accuracy.

### The intelligence layer

This is the part that matters, and it is operations research, not a model.
Deterministic, explainable, auditable — which is the correct choice for a
life-safety system, and the honest answer when someone asks where the AI is.

| Stage | Method | Where |
|---|---|---|
| Deduplicate | `ST_ClusterDBSCAN`, 300 m, partitioned by hazard | [clustering.py](backend/app/services/clustering.py) |
| Severity | five weighted terms + hard escalations, breakdown persisted | [scoring.py](backend/app/services/scoring.py) |
| Trust | provenance, corroboration, reporter history, rate signature | [scoring.py](backend/app/services/scoring.py) |
| Travel time | OSRM road network, haversine fallback | [routing.py](backend/app/services/routing.py) |
| **Assignment** | **Hungarian — provably optimal** | [assignment.py](backend/app/services/assignment.py) |
| Evacuation | min-cost flow, capacity as edge capacity | [shelters.py](backend/app/services/shelters.py) |
| The loop | debounce, commitment locking, per-district lock | [optimizer.py](backend/app/services/optimizer.py) |

**Why Hungarian rather than nearest-free-unit.** Boat A is 5 min from Incident 1
and 8 min from Incident 2. Boat B is 6 min from Incident 1 but 45 min from
Incident 2. Greedy serves the more severe incident first, takes Boat A, and
strands Boat B with a 45-minute drive: **50 minutes**. Hungarian gives up one
minute on the first incident to save 37 overall: **14 minutes**.

That example is a test, not a claim — `test_hungarian_beats_greedy` in
[test_assignment.py](backend/tests/test_assignment.py). If the pitch is ever
wrong, the suite goes red.

The **Greedy ↔ Optimized** toggle in the dashboard header reads two plans the
solver already computed and persisted, so the delta on the metric strip is a
real comparison rather than a re-roll.

---

## Data provenance

Stated plainly, because a judge may ask and the honest answer is stronger than
a vague one.

| Data | Source | Status |
|---|---|---|
| District boundary | Simplified Ganjam outline | modelled — swap for Survey of India / OSM relation |
| Shelters (62) | Real Ganjam institutions and block coordinates | plausible — swap for the Odisha SRC register |
| Resources (40) | Real block HQ coordinates, realistic agency/capability mix | **synthetic** — no public NDRF/ODRAF roster exists |
| Population grid | Modelled 1 km surface | modelled — swap for WorldPop / Census 2011 |
| Pincodes | Real Ganjam pincodes | real |
| CAP alerts | SACHET CAP 1.2 endpoint, ETag-cached | real feed; `fixtures/` for offline |
| Road network | OSRM + Odisha OSM extract | deferred — haversine fallback until enabled |
| Citizen reports | Generated by `seed/scenario.py` | **synthetic**, correctly so |

**There is no ML in this system and no training data.** The "predictive" in
predictive pre-positioning refers to IMD's forecast, arriving as a CAP polygon;
SETU adds a facility-location optimization over it. Demand forecasting and
image-based damage triage are on the roadmap, unbuilt and unclaimed.

---

## Layout

```
backend/app/
  services/     the intelligence layer — §6, the part that wins
  routers/      ingest, operations, websocket
  integrations/ CAP poller, SMS gateways and grammar
  workers/      optimizer loop + CAP loop
  seed/         Ganjam data and scenario generator
frontend-dashboard/   DEOC operating picture
frontend-pwa/         citizen reporting app
fixtures/             recorded CAP XML for offline runs
```

## Tests

```bash
make test                                # in the container
python -m pytest backend/tests -q        # locally
```

The solver tests are the important ones: they assert that Hungarian beats
greedy, that capability and capacity constraints are never violated, that a
committed en-route unit is never reassigned, and that shelter overflow never
exceeds free beds.

## Configuration

Every tunable lives in [config.py](backend/app/config.py). The ones worth
knowing:

| Variable | Default | Effect |
|---|---|---|
| `OFFLINE_MODE` | `true` | Serve CAP from fixtures, zero egress |
| `ROUTING_PROVIDER` | `haversine` | `osrm` once the extract is prepared |
| `SMS_PROVIDER` | `mock` | `twilio` for a real SMS on a judge's phone |
| `CLUSTER_EPS_M` | `300` | Dense town wants 150, sparse block 800 |
| `TRUST_QUARANTINE_THRESHOLD` | `0.35` | Below this, visible but never auto-dispatched |
