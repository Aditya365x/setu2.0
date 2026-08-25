# Deploying SETU

Two frontends on **Vercel**, the backend on **Railway**.

The split is forced by what the backend actually is. Vercel is serverless, and
SETU's worker is sixteen permanent asyncio loops blocking on Redis `BLPOP` —
that is not a function that returns, it is a process that must stay alive. The
WebSocket hub has the same problem. Everything else (PostGIS, Redis, the API)
*could* run on Vercel; the worker cannot, and without it nothing is ever
clustered, scored or dispatched.

> **Before you rely on this for a demo:** the whole premise of this system is
> that it works with the network unplugged, and PLAN.md's verification ends with
> "unplug the Wi-Fi and repeat end to end". A cloud deployment makes your
> strongest argument depend on venue Wi-Fi. Deploy it as a shareable link and
> demo from the laptop.

---

## 1. Backend on Railway

Four services in one Railway project.

### 1.1 Postgres with PostGIS

Railway's stock Postgres has **no PostGIS**, and every spatial query in §6 needs
it. Deploy the image directly instead:

* **New** → **Empty Service** → **Deploy from Docker Image**
* Image: `postgis/postgis:16-3.4`
* Variables:

  ```
  POSTGRES_USER=setu
  POSTGRES_PASSWORD=<something long>
  POSTGRES_DB=setu
  ```
* Add a volume mounted at `/var/lib/postgresql/data`, or the database is wiped
  on every redeploy.

### 1.2 Redis

* **New** → **Database** → **Redis**. Railway's own is fine; nothing spatial
  touches it.

### 1.3 API

* **New** → **GitHub Repo** → this repository
* **Root directory**: `backend`
* Railway reads `backend/railway.json` and builds the Dockerfile.
* Variables:

  ```
  DATABASE_URL=postgresql+asyncpg://setu:<password>@<pg-host>:5432/setu
  REDIS_URL=redis://default:<password>@<redis-host>:6379/0
  OFFLINE_MODE=true
  LIVE_CONDITIONS=true
  SEED_ON_START=true
  REPORTER_HASH_SALT=<random 32+ chars>
  JWT_SECRET=<random 32+ chars>
  ```

  `DATABASE_URL` must say `postgresql+asyncpg://`, not `postgresql://` —
  Railway's reference variable gives you the latter and SQLAlchemy will refuse
  to load the async driver.

  `SEED_ON_START=true` seeds all 16 districts on first boot, since there is no
  shell to run `make seed` in. Every step checks for existing rows, so restarts
  are a no-op. The first boot inserts ~72,000 population cells and takes a
  minute or two — expect the first health check to be slow.

  **Set the two secrets.** `REPORTER_HASH_SALT` is what makes the phone-number
  HMAC irreversible; shipping the dev default would de-anonymise every report.

* Generate a public domain. Note it — this is `VITE_API_ORIGIN`.

### 1.4 Worker

Same image, different command. This is the piece Vercel cannot host.

* **New** → **GitHub Repo** → same repository, root directory `backend`
* **Custom start command**: `python -m app.workers.run`
* Same variables as the API, except `SEED_ON_START` (leave it unset — the API
  already did it, and two seeders racing is pointless).
* No public domain. It talks to Postgres and Redis only.

---

## 2. Frontends on Vercel

Two projects from the same repository.

### 2.1 Dashboard

* **Add New** → **Project** → this repository
* **Root Directory**: `frontend-dashboard`
* Framework preset: **Vite** (`vercel.json` sets this anyway)
* Environment variable:

  ```
  VITE_API_ORIGIN=https://<your-api>.up.railway.app
  ```

Vite inlines this **at build time**, so changing it later needs a redeploy, not
just a restart.

### 2.2 Citizen PWA

* Same repository, **Root Directory**: `frontend-pwa`
* Environment variables:

  ```
  VITE_API_ORIGIN=https://<your-api>.up.railway.app
  VITE_BASEMAP_ORIGIN=https://<your-dashboard>.vercel.app
  ```

The basemap points at the **dashboard** deployment on purpose: the 48 MB PMTiles
archive is deployed with the dashboard, and shipping a second copy would double
the repository for a byte-identical file. The dashboard's `vercel.json` sets
`Access-Control-Allow-Origin` and exposes the `Range` headers on `/basemap/`
only, which is what lets the PWA read tiles cross-origin.

---

## 3. Verify

```bash
API=https://<your-api>.up.railway.app

curl -s $API/health
curl -s "$API/api/v1/districts" | head -c 300      # expect 16
curl -s "$API/api/v1/metrics?district_id=1"
```

Then, in the browser:

1. Dashboard loads with a district picker listing 16 districts.
2. The chip top-right reads **live**, not *reconnecting* — if it says
   reconnecting, the WebSocket is not reaching Railway (see below).
3. The map renders roads and place names, not a black rectangle.
4. Submit a report from the PWA; it appears in the dashboard queue.

Seed a demo board once the stack is up:

```
railway run --service <api> python -m seed.scenario cyclone_landfall --district 1
```

---

## Things that will actually go wrong

**"reconnecting" that never resolves.** The dashboard opens
`wss://<api-origin>/api/v1/ws`. If `VITE_API_ORIGIN` is `http://` rather than
`https://`, the page derives `ws://`, and a browser blocks that from an HTTPS
page as mixed content — silently. Symptom: metrics load fine, nothing ever
updates. Always use the `https://` origin.

**A blank dashboard with 500s.** Almost always `DATABASE_URL` missing the
`+asyncpg` driver.

**Map renders black.** The basemap is a 48 MB static file and may bump Vercel's
deployment size limits on the Hobby tier. The map degrades to a flat dark ground
with the incidents still on it — by design — so check the browser console for
`basemap unavailable` rather than assuming the map is broken.

**Cold worker.** Railway may idle a service with no HTTP traffic. The worker has
no public domain and serves no requests, so confirm it stays running; if it
sleeps, incidents will cluster but never get dispatched.

**SMS inbound will not work** without pointing a Twilio/Exotel webhook at
`$API/api/v1/ingest/sms`. Outbound and the mock gateway are unaffected.

---

## Local is unchanged

None of this alters `docker compose up`. `VITE_API_ORIGIN` unset means relative
URLs, which is exactly what nginx proxies today — verified: dashboard, PWA, both
API paths and HTTPS all still return 200 after these changes.
