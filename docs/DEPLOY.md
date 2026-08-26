# Deploying SETU

Two frontends on **Vercel**, the backend on **Render**.

The split is forced by what the backend actually is. Vercel is serverless, and
SETU's optimiser is sixteen permanent asyncio loops blocking on Redis `BLPOP` —
that is not a function that returns, it is a process that must stay alive. The
WebSocket hub has the same problem. Everything else (PostGIS, Redis, the API)
*could* run on Vercel; the optimiser cannot, and without it reports arrive and
cluster but nothing is ever scored, assigned or dispatched.

> **Before you rely on this for a demo:** the whole premise of this system is
> that it works with the network unplugged, and PLAN.md's verification ends with
> "unplug the Wi-Fi and repeat end to end". A cloud deployment makes your
> strongest argument depend on venue Wi-Fi. Deploy it as a shareable link and
> demo from the laptop.

---

## 1. Backend on Render

`render.yaml` at the repo root is a Blueprint: it declares every resource, on
**free plans throughout**, so you do not create them by hand and are not asked
for a card.

1. Render dashboard → **New** → **Blueprint**
2. Connect `github.com/Aditya365x/setu2.0`, branch `main`
3. Render reads `render.yaml` and proposes:

   | Resource | Type | Why |
   |---|---|---|
   | `setu-api` | Web (Docker), free | FastAPI — ingest, read models, WebSocket hub, **and the 16 optimiser loops** |
   | `setu-db` | PostgreSQL, free | PostGIS — every spatial query in §6 |
   | `setu-redis` | Key Value, free | optimise queue, pub/sub, caches |

4. **Apply**. First build takes several minutes (the API image is ~720 MB).

### What the Blueprint already handles

* **`DATABASE_URL`** is wired from the database automatically. Render injects
  `postgresql://…`, which SQLAlchemy's async engine rejects — `config.py`
  rewrites the prefix to `postgresql+asyncpg://`, so it just works. This used to
  be the single most likely deployment failure.
* **`REPORTER_HASH_SALT` and `JWT_SECRET`** use `generateValue: true`, so Render
  creates random secrets. The salt is what makes the phone-number HMAC
  irreversible — shipping the dev default would de-anonymise every report.
* **`RUN_WORKER_IN_API=true`** — see below.
* **`SEED_ON_START=true`** on the API only. First boot seeds all 16 districts
  (~73,000 rows, a minute or two). Every step checks for existing rows, so
  restarts are a no-op. Expect the first health check to be slow.

### Why there is no worker service

Render offers **no free plan for background workers** — the cheapest is paid.
That is the only reason a Blueprint for this app would ever ask for a card;
nothing in SETU needs a "pro" feature. Free web services, free Postgres and free
Key Value all exist.

But the optimiser is not something you can economise away. Drop it and reports
still arrive, still cluster into incidents, and are then never scored, assigned
or dispatched — a board that fills up and never moves.

So the Blueprint sets `RUN_WORKER_IN_API=true` and runs those loops inside the
API process. **Understand this as a trade, not a simplification:**

| | Dedicated worker (compose, paid Render) | Embedded (free Render) |
|---|---|---|
| Solver vs. request handling | separate processes | same event loop |
| Restart blast radius | either alone | both together |
| Idle behaviour | worker always awake | sleeps with the web service |

Also on free: the web service **sleeps after ~15 minutes idle**, which now takes
the optimiser down with it — open the dashboard a few minutes before you present
and let a cycle run. And **free Postgres expires after 90 days.**

If you do have a paid account, prefer the correct shape: set the API to
`plan: starter`, drop `RUN_WORKER_IN_API`, and add back a worker service running
the same image with `command: python -m app.workers.run`.

### After it is live

Copy the API URL (`https://setu-api-xxxx.onrender.com`) and check it:

```bash
API=https://setu-api-xxxx.onrender.com

curl -s $API/health
curl -s "$API/api/v1/districts" | head -c 300      # expect 16 districts
curl -s "$API/api/v1/metrics?district_id=1"
```

Seed a demo board from Render's shell (API service → **Shell**):

```bash
python -m seed.scenario cyclone_landfall --district 1     # optimiser demo
python -m seed.scenario cyclone_landfall --all            # every district
```

---

## 2. Frontends on Vercel

Two projects from the same repository.

### 2.1 Dashboard

* **Add New** → **Project** → `setu2.0`
* **Root Directory**: `frontend-dashboard`
* Environment variable:

  ```
  VITE_API_ORIGIN=https://setu-api-xxxx.onrender.com
  ```

Vite inlines this **at build time**. Changing it later needs a redeploy, not a
restart.

### 2.2 Citizen PWA

* Same repository, **Root Directory**: `frontend-pwa`
* Environment variables:

  ```
  VITE_API_ORIGIN=https://setu-api-xxxx.onrender.com
  VITE_BASEMAP_ORIGIN=https://<your-dashboard>.vercel.app
  ```

The basemap points at the **dashboard** deployment on purpose: the 47.6 MB
PMTiles archive ships with the dashboard, and a second copy would double the
repository for a byte-identical file. The dashboard's `vercel.json` sets
`Access-Control-Allow-Origin` and exposes `Content-Range` / `Accept-Ranges` on
`/basemap/` only, which is what lets the PWA read tiles cross-origin.

---

## 3. Verify in the browser

1. Dashboard loads; the district picker lists 16 districts.
2. The chip top-right reads **live**, not *reconnecting*.
3. The map shows roads and place names, not a black rectangle.
4. Submit a report from the PWA; it appears in the dashboard queue with a
   reference code.
5. Paste that code into the **REF** box — it should resolve to the incident.

---

## Things that will actually go wrong

**Cold starts.** Render free/starter services spin down when idle. The first
request after a sleep can take 30–60 seconds, and the worker restarting means a
brief gap in optimisation. Open the dashboard a few minutes before you present.

**"reconnecting" that never clears.** The dashboard opens
`wss://<api-origin>/api/v1/ws`. If `VITE_API_ORIGIN` is `http://` rather than
`https://`, the page derives `ws://`, and a browser silently blocks that from an
HTTPS page as mixed content. Metrics load, nothing ever updates. Always use the
`https://` origin.

**The map is the riskiest part of Vercel.** PMTiles is not a file you download —
it is a database read through HTTP Range requests, dozens per map view. If
Vercel's CDN does not honour `Range` on a 47.6 MB static asset, every tile read
pulls the whole archive. This has **not been verified**; test it first:

```bash
curl -s -D - -o /dev/null -H "Range: bytes=0-16383" \
  https://<your-dashboard>.vercel.app/basemap/east_coast.pmtiles | head -1
```

`206 Partial Content` means you are fine. `200` means the map will be unusable —
move the archive to object storage (Azure Blob, Cloudflare R2, S3, all of which
support Range natively) and repoint `VITE_BASEMAP_ORIGIN`.

The failure is quiet: the map degrades to a flat dark ground with the incidents
still on it, by design. You will not get an error, you will get "why is the map
black".

**Nothing is being dispatched.** Check the API log for the embedded optimiser
announcing itself at boot, then for cycles:

```
optimiser embedded in API — 16 district(s): Ganjam(1), Puri(2), …
[Ganjam] cycle(tick): 30 incidents, 40 units, mean 18.4 -> 21.1 min, 202ms
```

No first line means `RUN_WORKER_IN_API` did not reach the process. No cycles
after it means the service is asleep — hit any URL to wake it.

**SMS inbound** needs a Twilio/Exotel webhook pointed at
`$API/api/v1/ingest/sms`. Outbound and the mock gateway are unaffected.

---

## Local is unchanged

None of this alters `docker compose up`. `VITE_API_ORIGIN` unset means relative
URLs, which is exactly what nginx proxies today — verified after every change:
dashboard, PWA, both API paths and HTTPS all return 200, and 36 tests pass.
