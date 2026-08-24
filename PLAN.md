# SETU — Complete Build Plan (P0 → P2)

> Live copy. I update the progress table below as work lands, so this file is
> the single place to check status. Canonical plan text follows unchanged.

## Progress

| Phase | Item | Status |
|---|---|---|
| — | Repo scaffold, `docker-compose.yml`, Dockerfile | done |
| — | `app/config.py` — every Appendix B tunable | done |
| — | `app/schema.sql` — §5.2 DDL + §5.3 indexes | done |
| P0 | DB session layer (`db.py`) + Redis bus (`bus.py`) | done |
| P0 | FastAPI app shell + routers (`main.py`, `operations.py`) | done |
| P0 | Ingest path + `client_report_uuid` idempotency | done |
| P0 | WebSocket hub + Redis fan-out + backoff reconnect | done |
| P0 | Ganjam seed: 62 shelters, 40 units, pop grid, 25 pincodes | done |
| P0 | Dashboard (MapLibre + Zustand), builds clean | done |
| P0 | Citizen PWA + IndexedDB outbox, 50.9 kB gzipped | done |
| P0 | CAP poller, ETag + 304, offline fixture | done |
| **P0** | **GATE: phone → dashboard in under 2s** | **PASSED — 14ms ingest, 25ms read** |
| P1 | §6.1 clustering (ST_ClusterDBSCAN + merge) | done |
| P1 | §6.2 severity + §6.3 trust | done, 10 tests |
| P1 | §6.4 routing adapter (haversine + OSRM) | done |
| P1 | §6.5 Hungarian + greedy baseline | **done, 6 tests — 50min vs 14min verified** |
| P1 | §6.6 shelter min-cost flow | done, 5 tests |
| P1 | §6.8 optimizer cycle (debounce, lock, dual-strategy) | done |
| P1 | Heatmap, dispatch lines, breakdown panel, toggle | done |
| P1 | §9 SMS grammar + IVR + gateway adapters | done |
| **P1** | **GATE: Greedy↔Optimized delta is real** | **PASSED — see numbers below** |
| P2 | Items 1–10, priority-ordered | todo (scenario runner already built) |
| — | 25 tests passing; both frontends build | done |

## Measured, on the running system

Full stack up on Docker, Ganjam seeded, Cyclone Landfall scenario (200 reports over 90s):

| | Greedy | Optimized | |
|---|---|---|---|
| Mean response | 15.9 min | **13.7 min** | −14% |
| Worst case | 60.0 min | **44.8 min** | −25% |
| Incidents served | 23 | **24** | +1 |
| Critical unassigned | — | **1** | |

200 reports → 26 incidents. Full optimisation cycle **144 ms** (target < 1.5 s).
Ingest **14 ms** (target p99 < 300 ms). ETAs are haversine-degraded, as chosen.

## Bugs found and fixed during verification

Four were the kind that produce a plausible dashboard and a wrong pitch:

1. **`ST_ClusterDBSCAN` was given degrees.** `eps := 300` meant 300 *degrees*, not metres — the entire district collapsed into one incident per hazard. Now projected to UTM 45N before clustering.
2. **`make reset` destroyed the resource roster.** `TRUNCATE incidents CASCADE` also truncates `resources`, which holds an FK to it. Would have wiped the unit roster mid-rehearsal. Now a DELETE in FK order, in [backend/seed/reset.sql](backend/seed/reset.sql), with a seed-intact assertion.
3. **People were double-counted across duplicate reports.** `people_affected_est` summed across the cluster, so ten witnesses saying "3 inside" became 30 — which then exceeded every unit's capacity and silently made incidents unassignable. Now MAX, not SUM.
4. **The Greedy↔Optimized comparison flattered greedy.** Comparing mean response over each strategy's own served set is not like-for-like: greedy strands the hard incidents and averages over what is left. Coverage is now reported alongside, and the NDRF reserve preference was cut from 50 minutes to 8 so it tie-breaks rather than distorts.

Also fixed: asyncpg cannot run multi-statement DDL (raw connection), API/worker raced on `CREATE EXTENSION` (advisory lock), CAP timestamps passed as strings, SRID mismatch in the population KNN lookup, and the SMS parser ate the "3" out of "water 3 foot".

## The "Sending…" hang

Same root cause as the GPS failure, one layer down. `crypto.randomUUID()` is
also a secure-context-only API, so on a plain-HTTP LAN address it is
`undefined` and building the report record threw — *after* `setBusy(true)` and
outside the `try`. The button then read "Sending…" forever with no error, which
is indistinguishable from a broken app to someone who needs help now.

Three fixes, because any one of them alone would have left a sharp edge:

1. **`outbox.newId()`** builds an RFC4122 v4 id from `crypto.getRandomValues`,
   which is *not* secure-context gated, falling back further for old browsers.
   Verified: 5000 ids, all unique and well-formed, with `randomUUID` undefined.
   This id is the server's idempotency key, so it has to exist before anything
   else happens.
2. **`try/finally` around the whole submit**, so no failure can strand the
   button. Double-submit is guarded too.
3. **A 12-second fetch timeout** — `fetch()` has none by default, so a
   congested tower could hang the UI indefinitely. On timeout the report falls
   into the offline queue, which is where it belonged anyway.

If IndexedDB itself fails there is no safe copy of the report, so the app now
says so and suggests SMS rather than claiming the report is on its way.

## GPS / secure-context fix

Reported as "the app is not working because of GPS". Two causes, one of them a
hard bug:

1. **The PWA was served over plain HTTP.** Browsers only expose
   `navigator.geolocation` in a secure context; `localhost` is exempt, so it
   worked on the laptop and failed silently on any phone hitting the LAN IP.
   Fixed: the PWA container now generates a self-signed certificate at startup
   (SAN covers `localhost` plus whatever `CERT_HOSTS` names) and serves HTTPS on
   **5443** alongside HTTP on 5174. Works offline.
2. **Submit was hard-blocked on `!position`.** Any GPS failure made the app
   unusable, and the spec's manual fallback was never built. Fixed: the app now
   distinguishes insecure-context / denied / unavailable / timeout /
   unsupported and says which, and offers a PIN code fallback resolved via a new
   `/api/v1/geocode/pincode/{pin}` endpoint against the same table the SMS
   channel uses. Unknown pincodes fall back to the district centroid with an
   honestly-stated 25 km accuracy. A late coarse GPS fix can no longer overwrite
   a better manual answer.

Verified end to end over `https://192.168.2.102:5443`: pincode 761008 resolves
to Chikiti at 3 km, the report is accepted, and trust drops to 0.40 — above the
0.35 quarantine floor, so it still dispatches.

## Decisions taken since approval

- **Build lives at the repo root**, not in a `setu/` subfolder. Docs moved to [docs/](docs/).
- **No ML.** Confirmed OR-only per §16 — the "prediction" in pre-positioning is IMD's CAP polygon; SETU adds facility-location optimization over it, nothing is trained. Demand forecasting and image damage triage stay on the §18 roadmap, unbuilt and unclaimed.
- **Seed data**: geographically plausible Ganjam seed now so the pipeline runs today; real OSDMA shelter names swap in before the demo via a documented script. Resource roster is synthetic-but-realistic at real station coordinates — labelled as such.
- **Schema via `schema.sql`**, applied idempotently on startup, rather than Alembic. Faster to iterate under time pressure and keeps §5.2 readable as the source of truth; Alembic can wrap it later if the pilot needs migrations.

---

## Context

`d:\Sih_2026_⚡⚡⚡⚡⚡⚡` currently contains only two documents: `SETU_Disaster_Platform_Plan (1).pdf` (a 53-page technical design and build plan) and `SETU_Presentation.pptx` (a 14-slide pitch deck). There is no code yet.

SETU is a district-scoped disaster coordination platform for SIH 2026. Its thesis: India's *detection* (IMD/CWC/INCOIS) and *dissemination* (NDMA SACHET) are solved national capabilities; *coordination* and *allocation* — the six hours between the warning and the rescue — run on WhatsApp, phone calls and paper registers. SETU consumes official CAP alerts, opens a return channel from citizens across four degrading ingest channels, deduplicates a flood of reports into incidents, and solves the resource-allocation problem optimally.

The spec is emphatic that §6 (the intelligence layer) is the differentiator and must be protected from scope creep — every competing team demos a map with pins; almost none demos a solver.

This is the **complete build plan, P0 through P2** — skeleton, engine, then the polish that scores. Each phase has a hard gate and nothing proceeds until the previous gate passes. **P0 + P1 are the committed core**; P2 is priority-ordered so that if time runs short, work stops at a coherent boundary rather than mid-feature.

**Decisions taken:** routing starts on the haversine ÷ 8.33 m/s fallback the spec already defines as OSRM-down degradation behaviour, behind a routing adapter, so OSRM becomes a config swap later; seed data models **Ganjam, Odisha** (matches every coordinate in the spec, e.g. 19.3149, 84.7941); solo build.

---

## Architecture

One `docker compose up --build` brings up the whole stack, offline-capable:

| Service | Image / source | Role |
|---|---|---|
| `db` | `postgis/postgis:16-3.4` | Authoritative state; all spatial ops in SQL |
| `redis` | `redis:7-alpine` | Optimizer job queue, WebSocket pub/sub fan-out, CAP ETag cache |
| `api` | `./backend` (uvicorn) | Ingest, read models, WebSocket hub |
| `worker` | `./backend` | CAP poller + optimizer worker |
| `dashboard` | `./frontend-dashboard` | DEOC operating picture |
| `pwa` | `./frontend-pwa` | Citizen reporting app |

`osrm` is written into `docker-compose.yml` but commented out with the prep commands documented, so it drops in without code changes.

**Two pluggable adapters, both following the `SmsGateway` Protocol idiom the spec defines in §9.1** — same shape, selected by env var:
- `services/routing.py` — `HaversineRouter` (default) / `OsrmRouter`, both returning the `[resources × incidents]` seconds matrix from §6.4.
- `services/storage.py` — `LocalDiskStorage` (default, served by FastAPI static) / `MinioStorage`. This is a deliberate simplification of the spec's MinIO-only §4.2 line; it removes signed-URL work from P0 without changing any caller.

---

## Repository layout

Follow §12.1 verbatim — it is already a good structure and matching it keeps the doc usable as the architecture reference:

```
setu/
  docker-compose.yml   Makefile   README.md
  backend/app/{main,config,db}.py
    models/  schemas/
    routers/{ingest,incidents,resources,shelters,assignments,alerts,metrics,ws}.py
    services/{clustering,scoring,routing,assignment,shelters,optimizer,storage}.py
    integrations/{cap.py, weather.py, sms/{base,twilio,mock}.py}
    workers/{cap_poller.py, optimizer_worker.py}
    alembic/  tests/  seed/
  frontend-dashboard/  frontend-pwa/  fixtures/
```

`services/preposition.py` (§6.7) and `routers/{simulate,replay}.py` arrive in P2.

---

## Phase P0 — Skeleton

**Gate: a report submitted from a phone appears on the dashboard in under 2 seconds.** Nothing downstream matters until this passes.

1. **Schema and migrations.** Port §5.2 DDL exactly — `districts`, `alerts`, `reports`, `incidents`, `resources`, `shelters`, `assignments`, `audit_log`, plus the `report_source` / `hazard` / `resource_type` enums. Add every GIST and partial index from §5.3; the clustering query is unusable without them. SQLModel models mirror the DDL; migrations via Alembic.

2. **Seed data** (`backend/seed/`) — Ganjam district boundary polygon, 50–80 real cyclone-shelter names/coords/capacities from the Odisha SRC list, 40 resources in the §A mix (8 rescue teams, 10 boats, 6 ambulances, 4 medical teams, 8 supply trucks, 4 heavy equipment; agencies NDRF/ODRAF/Fire/NGO), and a 200-report scripted scenario clustered along the river and coastal strip with 6–10 duplicates per genuine incident. The spec is explicit that "Shelter A / Shelter B" costs marks on completeness and impact.

3. **Ingest path.** `POST /api/v1/ingest/report` (multipart, optional photo) → validate → INSERT `reports` (status `received`) → store photo via the storage adapter → `LPUSH optimize:{district_id}` → return `202` with `{report_id, reference_code}` in under 200 ms. Server keys on `client_report_uuid` for idempotency — this is what makes offline outbox replay safe, so it goes in on day one, not later.

4. **WebSocket hub** (`routers/ws.py`) — subscribes to Redis `district:{id}:updates`, fans out the §7.3 event envelope. Client side gets exponential-backoff reconnect (1s→30s) plus full resync of `/incidents` and `/assignments` immediately; the spec warns the demo laptop will sleep.

5. **Dashboard shell.** React + Vite + MapLibre GL + Zustand. Single screen, no tabs: incident queue left, map centre, detail panel right, metric strip top (§8.1). Map updates go through `source.setData()` on WebSocket events — never re-render the MapLibre component.

6. **PWA report form.** React + Vite + Workbox. Geolocation with `enableHighAccuracy` (record `accuracy`, manual map-pin fallback on denial), `<input type="file" capture="environment">` downscaled to 1280px / JPEG q0.7 on canvas before upload, IndexedDB outbox with Background Sync, visible "N reports queued" badge. Persist to IndexedDB *first*, then attempt the network — that ordering is the whole point of the offline path.

7. **CAP poller** (`integrations/cap.py`) — 60s poll with `If-None-Match`, handle 304 as the cheap common case, parse CAP 1.2 `<info>` blocks, upsert idempotent on `cap_identifier`, skip alerts not intersecting the district boundary, publish `alert.new`. Cache the last-good XML in Redis and show a staleness banner rather than a blank map on failure. `fixtures/` holds recorded CAP XML so the whole thing runs with the network unplugged.

---

## Phase P1 — The engine

**Gate: the Greedy↔Optimized toggle yields a real, computed delta and the metric strip updates live.** Roughly 40% of effort belongs here.

1. **`services/clustering.py` (§6.1)** — the `ST_ClusterDBSCAN` query, `eps=300`, `minpoints=1`, `PARTITION BY hazard_type`, 30-minute rolling window, trust floor 0.35. Partitioning by hazard is not optional: a medical call and a flood report 50m apart are two incidents needing two capabilities. Before creating a new incident, `ST_DWithin(existing.centroid, new_centroid, 300)` with matching hazard and `status='open'` → attach and recompute rather than fragment.

2. **`services/scoring.py` (§6.2, §6.3)** — the five weighted severity terms (reported 0.35 / corroboration 0.20 / hazard 0.15 / population 0.15 / official 0.15), log-scaled capped corroboration, hard escalations for medical and building-collapse, and the ageing term. Persist the component breakdown to `severity_breakdown` JSONB and render it in the detail panel — a black box that outputs 87 is useless to a Collector who must justify the decision. Trust scoring gates the quarantine queue at 0.35; low-trust reports are visible and never auto-dispatched, never silently dropped.

3. **`services/routing.py` (§6.4)** — adapter interface returning the seconds matrix. `HaversineRouter` divides great-circle distance by 8.33 m/s (≈30 km/h effective disaster-conditions road speed). Same code path OSRM degrades into, so the swap is a one-line config change and the degraded-ETA UI chip is exercised from day one.

4. **`services/assignment.py` (§6.5) — the differentiator.** `build_cost_matrix` with hard constraints as `+BIG` (capability mismatch, insufficient capacity, unavailable status, resolved incident) and soft preferences (severity pull via `URGENCY_WEIGHT`, `SLA_BREACH_PENALTY`, `COMMITMENT_BONUS` / `REASSIGNMENT_PENALTY` anti-thrash, NDRF-for-hard-jobs). `strategy="optimized"` → `scipy.optimize.linear_sum_assignment`; `strategy="greedy"` → severity-ordered nearest-free-unit baseline. Both persisted with `strategy` and `solver_run_id` so the toggle reads real stored plans, not a re-simulation.

5. **`services/shelters.py` (§6.6)** — `networkx.max_flow_min_cost` over SRC→incident→shelter→SNK with free beds as edge capacity and travel minutes as weight, +30 penalty when an incident needs medical and the shelter has none. `unplaced > 0` raises a `SHELTER_CAPACITY_SHORTFALL` alarm — a real operational number no existing system surfaces.

6. **`services/optimizer.py` (§6.8)** — the cycle, on new report / status change / alert / 30s tick. Three rules keep it stable and all three are load-bearing: **debounce** 2s so an 80-report burst triggers one run not eighty; **commitment locking** so a committed en-route pairing is a fixed input to every later run (without this the map thrashes and the demo looks broken); **per-district Redis lock** so two runs never conflict. Solve both strategies each cycle, persist the comparison to drive `/metrics`. 50km spatial pre-filter keeps the matrix small.

7. **State machines (§5.4)** with the invariant enforced twice — in SQL (`WHERE status='idle'`) and re-asserted in the worker: a resource in `enroute` or `onsite` can never enter the solver's free pool. The spec notes every live-demo dispatch bug traces back to violating this.

8. **Dashboard §6 surfacing** — severity-weighted heatmap (weight = `severity_score`, *not* point count; a count-weighted heatmap just maps phone density), incident pins with radius ∝ people affected, dispatch lines solid=committed / dashed=proposed with ETA labels, quarantine queue, SLA countdowns, commit/override flow writing `audit_log`, and the Greedy↔Optimized header toggle driving the metric strip.

9. **SMS / IVR (§9)** — `SmsGateway` Protocol with `MockGateway` (default, drives an offline simulator UI) and `TwilioGateway`. Tolerant grammar parser `<HAZARD> <SEVERITY> <PINCODE> [landmark]` with the multilingual keyword table; missing severity → 3, unknown hazard → `other` flagged for review, unparseable → still ingested as a raw operator-queue report. Never silently drop a message from someone who may be in the water. Pincode → centroid sets `gps_accuracy_m=3000`, which correctly lowers trust and renders an uncertainty circle instead of a false-precision pin. IVR DTMF tree via TwiML `<Gather>`.

---

## Phase P2 — The polish that scores

**Gate: hard feature freeze at T−4h, then rehearse the four-minute demo script (§14) ten times with a stopwatch.** Built in this priority order — the ordering is the plan, because each item down the list is a cleaner place to stop than the middle of the one above it.

1. **Simulation mode** (`routers/simulate.py`) — `POST /api/v1/simulate/{scenario}` spawns a scripted event stream. "Cyclone Landfall": ~200 reports over 90 seconds with a realistic hazard mix, severity distribution, spatial clustering along the river and coastal strip, and 6–10 duplicates per genuine incident. "Flash Flood": ~80 reports, faster onset, tighter geography — the second scenario to run when a judge asks to see it again. **Build this before anything else in P2; the entire demo runs off it.**

2. **Predictive pre-positioning** (`services/preposition.py`, §6.7) — the novelty claim and the highest single-item scoring value in P2. On `alert.new`: rasterize the CAP polygon into a 1km demand grid; weight cells by population 0.40 / historical incident density 0.25 / CAP severity 0.20 / road fragility 0.15; beam-limited weighted p-median (beam 200) over 30–60 candidate staging points (shelters, police stations, block HQs); assign idle resources with the **same Hungarian solver already built**. Persist as `assignments(kind='preposition')`, surface as an amber banner with arrowed amber move-lines, distinct from dispatch. Roughly 4–5 hours because it reuses the ETA matrix and solver. Do not reach for a MILP solver. If time collapses, substitute weighted k-means over demand cells snapped to the nearest candidate — visually and narratively identical, one hour.

3. **Metrics strip with the live greedy-vs-optimized comparison** — `GET /api/v1/metrics` per the §7.2 contract, driving open incidents, critical unassigned, units free, mean response `{optimized, greedy}`, worst case `{optimized, greedy}`, people evacuated, shelter occupancy, shortfall. This is what converts the algorithm into a number a judge can repeat.

4. **Real shelter data** from the Odisha SRC cyclone-shelter list, if not already fully loaded in P0 seeding — capacities 200–1,000, real names and coordinates.

5. **Backup demo video + `make reset`** — record a flawless 3-minute screen capture; bind `make reset` to a single terminal alias so the demo can restart in five seconds.

6. **SLA countdown timers and the priority triage queue** — makes the operating picture feel operational rather than decorative.

7. **After-action report export** — `GET /api/v1/aar/{event_id}.pdf` generated from the append-only `audit_log`. Government evaluators care about this disproportionately.

8. **Bilingual UI** — i18n JSON for English / Hindi / Odia in both dashboard and PWA. Cheap, and evaluators consistently notice it.

9. **Replay / digital-twin mode** — `GET /api/v1/replay?from=&to=` returns the time-ordered event stream; a scrub control plays the event back minute by minute. Reuses the audit log already built.

10. **Load-test numbers** — `make load` (locust, 500 reports/min for 5 min), report p50/p95/p99 ingest latency, optimizer cycle time under load, error rate. Target 500 reports/min sustained, 0 errors, p99 < 300 ms. ~1 hour, and it buys most of the feasibility parameter.

**The last four hours are not for features.** Seed realistic data, run the demo end to end at least ten times, record the backup video, prepare the second mirrored laptop. Every team that codes to the buzzer demos a crash.

---

## Effort allocation (§13.1)

| Workstream | Share |
|---|---|
| Prototype completeness (§4, 5, 7, 8) | 40% |
| Intelligence layer (§6) | 20% |
| Presentation and rehearsal (§14) | 15% |
| Problem understanding / field research | 10% |
| Feasibility artifacts and benchmarks (§11) | 8% |
| Impact and scalability material (§15, 17) | 7% |

If something has to be cut, cut from §8, never from §6. But note the doc's closing discipline: teams lose on their **weakest** parameter, not their strongest — when tempted to keep polishing an optimizer already at nine out of ten, spend the time on whatever is at five.

---

## Makefile — build these first

Per §12.2, described as the highest-ROI thirty minutes of the whole build:

```
make up      make seed     make demo
make reset   # truncate operational tables, keep seed — used 20× during rehearsal
make load    # locust, 500 reports/min
```

---

## Tests worth writing under time pressure (§C)

Write **`test_hungarian_beats_greedy`** first — it protects the central claim. The constructed 2×2 case from §6.5: Boat A 5min/8min, Boat B 6min/45min; greedy totals 50 min, Hungarian 14 min. Then: clustering correctness (10 reports within 200m of one hazard → exactly 1 incident; a different hazard 50m away → a second), hard constraints never violated, committed units never reassigned, shelter overflow placement never exceeds free beds, outbox replay creates exactly one report per `client_report_uuid`, malformed SMS ingested not dropped, CAP re-poll creates no duplicate alert.

---

## Verification

**P0 gate — end to end, on hardware:**
1. Start Docker Desktop (daemon is currently not running), then `make up && make seed`.
2. Open the dashboard; confirm the Ganjam boundary, 50+ real shelters and 40 resources render.
3. Open the PWA on a phone on the same network via QR. Submit a geo-tagged report with a photo. It must appear on the dashboard **in under 2 seconds, on a different machine.** Time it.
4. Switch the phone to aeroplane mode, submit again — the queued badge must appear. Reconnect — it must sync and land on the map, with exactly one report created.
5. `pytest backend/tests` green.

**P1 gate:**
6. `make reset && make demo` — 200 seeded reports ingest and collapse into ~12 incidents. Verify the count, and check the worker log for the cluster query timing.
7. Open an incident detail panel — the five-term severity breakdown must render with real numbers, not a bare score.
8. Flip Greedy↔Optimized in the header. Dispatch lines must redraw and the metric strip must show a computed non-trivial delta in mean response, worst case, and critical-unassigned. Confirm the numbers come from two persisted `assignments` rows sets, not a hardcoded pair.
9. Fill the nearest shelter past capacity; confirm overflow routes to the next-cheapest and that forcing district-wide shortage raises the shortfall alarm.
10. Send an SMS through the mock gateway; confirm it lands as a pin with an uncertainty circle and that the optimizer treats it identically to a PWA report.
11. Commit an assignment, then force a re-optimization — the committed unit must not be reassigned.
12. **Unplug the Wi-Fi and repeat step 6 end to end.** The spec's demo-day rule: the offline path is not a fallback for the demo, it is the demo.

**P2 gate:**
13. `POST /api/v1/simulate/cyclone-landfall` — 200 pins appear over 90 seconds and visibly collapse into ~12 incidents without the dashboard stuttering.
14. Inject a CAP alert from `fixtures/` with no incidents open — the amber pre-position banner and arrowed move-lines must appear, and committing must move idle units before any incident exists.
15. Export an AAR PDF; confirm every commit, override and resolution appears with actor, timestamp and reason.
16. Toggle Hindi and Odia in both dashboard and PWA.
17. `make load` — record p50/p95/p99 and cycle time under load.
18. **Run the full §14 four-minute script with a stopwatch, ten times, with the Wi-Fi off, using `make reset` between runs.** The most common judging failure is running long and being cut off before the optimizer toggle — which is the entire pitch.

---

## Explicitly out of scope

The spec's permanent non-goals, to be named as deliberately deferred on the roadmap slide rather than left unmentioned: auth/RBAC beyond a hardcoded role switch, drone or satellite feeds, blockchain, custom ML models, native mobile apps, Kubernetes, multi-district UI switching, real-time video, weather forecasting, and public mass alerting (SACHET already owns it — we consume it).

Never claim ML that was not built. The core is operations research — optimal assignment, min-cost flow, facility location — plus spatial clustering, and for a life-safety system that is the stronger answer: deterministic, explainable, auditable.
