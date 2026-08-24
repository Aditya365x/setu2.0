-- SETU schema — ported from §5.2 of the technical design.
-- Idempotent: safe to re-run. `make reset` truncates rather than dropping.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── tenancy ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS districts (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    state       TEXT NOT NULL,
    lgd_code    TEXT UNIQUE,                        -- official LGD district code
    boundary    geography(POLYGON,4326) NOT NULL,
    centroid    geography(POINT,4326) NOT NULL,
    timezone    TEXT DEFAULT 'Asia/Kolkata'
);

-- ── official alerts (CAP) ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts (
    id             BIGSERIAL PRIMARY KEY,
    district_id    INT REFERENCES districts(id),
    cap_identifier TEXT UNIQUE NOT NULL,            -- idempotency key
    source_agency  TEXT,                            -- IMD | CWC | INCOIS | GSI
    event          TEXT,
    severity       TEXT,                            -- Extreme|Severe|Moderate|Minor
    urgency        TEXT,                            -- Immediate|Expected|Future
    certainty      TEXT,
    headline       TEXT,
    instruction    TEXT,
    area_polygon   geography(MULTIPOLYGON,4326),
    effective_from TIMESTAMPTZ,
    expires_at     TIMESTAMPTZ,
    raw_xml        TEXT,
    ingested_at    TIMESTAMPTZ DEFAULT now()
);

-- ── enums ──────────────────────────────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE report_source AS ENUM ('app','sms','ivr','field_unit','manual');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE hazard AS ENUM ('flood','landslide','cyclone_damage','building_collapse',
                                'medical','fire','stranded','power_line','other');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE resource_type AS ENUM ('rescue_team','boat','ambulance','medical_team',
                                       'supply_truck','heavy_equipment','volunteer_group');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── incidents (clustered reports) ──────────────────────────────────────────
-- Declared before `reports` so the FK resolves; the doc lists them the other
-- way round for readability.
CREATE TABLE IF NOT EXISTS incidents (
    id                  BIGSERIAL PRIMARY KEY,
    district_id         INT NOT NULL REFERENCES districts(id),
    centroid            geography(POINT,4326) NOT NULL,
    hazard_type         hazard NOT NULL,
    severity_score      NUMERIC(5,2) DEFAULT 0,     -- 0..100
    severity_breakdown  JSONB,                      -- explainability payload
    report_count        INT DEFAULT 1,
    people_affected_est INT,
    needs_medical       BOOLEAN DEFAULT false,
    status              TEXT DEFAULT 'open',        -- open|assigned|onsite|resolved|false_alarm
    sla_deadline        TIMESTAMPTZ,
    opened_at           TIMESTAMPTZ DEFAULT now(),
    resolved_at         TIMESTAMPTZ
);

-- ── citizen reports ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reports (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    district_id         INT NOT NULL REFERENCES districts(id),
    geom                geography(POINT,4326) NOT NULL,
    gps_accuracy_m      INT,                        -- 5 for GPS, 3000 for pincode
    hazard_type         hazard NOT NULL,
    severity_raw        SMALLINT CHECK (severity_raw BETWEEN 1 AND 5),
    description         TEXT,
    photo_url           TEXT,
    photo_exif_ts       TIMESTAMPTZ,
    source              report_source NOT NULL,
    reporter_hash       TEXT,                       -- HMAC(phone, salt) — never raw PII
    people_reported     INT,
    trust_score         NUMERIC(4,3) DEFAULT 0.5,
    trust_breakdown     JSONB,
    incident_id         BIGINT REFERENCES incidents(id),
    status              TEXT DEFAULT 'received',    -- received|clustered|quarantined|resolved
    reference_code      TEXT UNIQUE,                -- short code SMS'd back
    client_report_uuid  TEXT UNIQUE,                -- idempotency for offline replay
    raw_text            TEXT,                       -- unparseable SMS kept verbatim
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- ── resources ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS resources (
    id                    BIGSERIAL PRIMARY KEY,
    district_id           INT NOT NULL REFERENCES districts(id),
    name                  TEXT NOT NULL,
    type                  resource_type NOT NULL,
    agency                TEXT,                     -- NDRF | ODRAF | Fire | NGO
    capabilities          TEXT[] DEFAULT '{}',      -- {water_rescue,medical,cutting}
    home_geom             geography(POINT,4326) NOT NULL,
    current_geom          geography(POINT,4326) NOT NULL,
    capacity              INT DEFAULT 1,            -- people rescuable / units of supply
    load                  INT DEFAULT 0,
    status                TEXT DEFAULT 'idle',      -- idle|enroute|onsite|returning|offline
    committed_incident_id BIGINT REFERENCES incidents(id),
    contact               TEXT,
    last_ping_at          TIMESTAMPTZ DEFAULT now()
);

-- ── shelters ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS shelters (
    id               BIGSERIAL PRIMARY KEY,
    district_id      INT NOT NULL REFERENCES districts(id),
    name             TEXT NOT NULL,
    geom             geography(POINT,4326) NOT NULL,
    capacity_total   INT NOT NULL,
    occupancy        INT DEFAULT 0,
    has_medical      BOOLEAN DEFAULT false,
    has_power        BOOLEAN DEFAULT false,
    has_water        BOOLEAN DEFAULT false,
    status           TEXT DEFAULT 'open',           -- open|full|closed|inaccessible
    contact          TEXT,
    last_verified_at TIMESTAMPTZ
);

-- ── pincode centroids (SMS/IVR geocoding, §9.2) ───────────────────────────
-- ~2-5 km accuracy. Stored honestly as gps_accuracy_m = 3000 on the report so
-- trust drops and the operator sees an uncertainty circle, not a false pin.
CREATE TABLE IF NOT EXISTS pincodes (
    pincode     TEXT NOT NULL,
    district_id INT NOT NULL REFERENCES districts(id),
    name        TEXT,
    geom        geography(POINT,4326) NOT NULL,
    PRIMARY KEY (pincode, district_id)
);

-- ── population grid (1 km cells) ───────────────────────────────────────────
-- Feeds the §6.2 equity term and, in P2, the §6.7 demand surface. Seeded from
-- WorldPop/Census; a plausible modelled grid until the real raster is clipped.
CREATE TABLE IF NOT EXISTS population_cells (
    id          BIGSERIAL PRIMARY KEY,
    district_id INT NOT NULL REFERENCES districts(id),
    geom        geography(POINT,4326) NOT NULL,
    density     NUMERIC(10,2) NOT NULL,      -- people per sq km
    historical_incident_density NUMERIC(6,3) DEFAULT 0,
    road_fragility NUMERIC(4,3) DEFAULT 0
);

-- ── assignments (solver output) ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS assignments (
    id            BIGSERIAL PRIMARY KEY,
    district_id   INT NOT NULL REFERENCES districts(id),
    incident_id   BIGINT REFERENCES incidents(id),
    resource_id   BIGINT REFERENCES resources(id),
    shelter_id    BIGINT REFERENCES shelters(id),
    kind          TEXT NOT NULL,                    -- dispatch | preposition | evacuation
    eta_seconds   INT,
    cost          NUMERIC(10,3),
    people        INT,                              -- evacuation flow volume
    solver_run_id UUID,                             -- traceability to one solver run
    strategy      TEXT,                             -- greedy | optimized (for the toggle)
    status        TEXT DEFAULT 'proposed',          -- proposed|committed|completed|cancelled
    created_at    TIMESTAMPTZ DEFAULT now(),
    committed_at  TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ
);

-- ── solver runs (drives the Greedy<->Optimized metric strip) ───────────────
CREATE TABLE IF NOT EXISTS solver_runs (
    id                  UUID PRIMARY KEY,
    district_id         INT NOT NULL REFERENCES districts(id),
    n_incidents         INT,
    n_resources         INT,
    mean_response_opt   NUMERIC(6,2),
    mean_response_greedy NUMERIC(6,2),
    worst_case_opt      NUMERIC(6,2),
    worst_case_greedy   NUMERIC(6,2),
    unassigned_critical_opt    INT,
    unassigned_critical_greedy INT,
    cycle_ms            INT,
    degraded_eta        BOOLEAN DEFAULT false,
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- Added after the first end-to-end run: comparing mean response over each
-- strategy's own served set flatters greedy, because greedy leaves the hard
-- incidents unassigned and averages over an easier subset. Coverage has to be
-- reported alongside it or the number is misleading.
ALTER TABLE solver_runs ADD COLUMN IF NOT EXISTS served_opt INT;
ALTER TABLE solver_runs ADD COLUMN IF NOT EXISTS served_greedy INT;
ALTER TABLE solver_runs ADD COLUMN IF NOT EXISTS total_response_opt NUMERIC(8,2);
ALTER TABLE solver_runs ADD COLUMN IF NOT EXISTS total_response_greedy NUMERIC(8,2);

-- ── audit (append-only) ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    district_id INT NOT NULL,
    actor       TEXT NOT NULL,                      -- user id or 'system:optimizer'
    action      TEXT NOT NULL,                      -- commit_assignment | override | resolve
    entity_type TEXT,
    entity_id   TEXT,
    before      JSONB,
    after       JSONB,
    reason      TEXT,
    ts          TIMESTAMPTZ DEFAULT now()
);

-- ── §5.3 spatial indexes: mandatory, everything below depends on them ──────
CREATE INDEX IF NOT EXISTS idx_reports_geom   ON reports   USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_incidents_geom ON incidents USING GIST (centroid);
CREATE INDEX IF NOT EXISTS idx_resources_geom ON resources USING GIST (current_geom);
CREATE INDEX IF NOT EXISTS idx_shelters_geom  ON shelters  USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_alerts_area    ON alerts    USING GIST (area_polygon);

-- ── hot query paths ────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_reports_open ON reports (district_id, created_at DESC)
    WHERE incident_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_incidents_active ON incidents (district_id, severity_score DESC)
    WHERE status IN ('open','assigned');
CREATE INDEX IF NOT EXISTS idx_resources_free ON resources (district_id, type)
    WHERE status = 'idle';
CREATE INDEX IF NOT EXISTS idx_popcells_geom ON population_cells USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_assignments_run ON assignments (district_id, solver_run_id, strategy);

-- ── vulnerable groups (§6, §22, §26) ──────────────────────────────────────
-- Who is affected matters as much as how many. Five trapped adults and five
-- trapped children are not the same dispatch decision, and a system that cannot
-- express the difference forces the operator to re-derive it from free text.
--
-- Stored per report because that is where the witness observes it, and rolled
-- up to the incident with bool_or: if any witness to an event reports children
-- present, the incident involves children.
ALTER TABLE reports ADD COLUMN IF NOT EXISTS has_children  BOOLEAN DEFAULT false;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS has_elderly   BOOLEAN DEFAULT false;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS has_injured   BOOLEAN DEFAULT false;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS has_disabled  BOOLEAN DEFAULT false;

ALTER TABLE incidents ADD COLUMN IF NOT EXISTS has_children  BOOLEAN DEFAULT false;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS has_elderly   BOOLEAN DEFAULT false;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS has_injured   BOOLEAN DEFAULT false;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS has_disabled  BOOLEAN DEFAULT false;

-- §21 — a reporter who moves during an evacuation. The original position is
-- kept in `geom`; this records that the location was revised and when, so the
-- operator can tell a corrected pin from a stale one.
ALTER TABLE reports ADD COLUMN IF NOT EXISTS last_location_update TIMESTAMPTZ;

-- Scale path (not needed at hackathon scale; state it in the pitch):
-- reports & audit_log PARTITION BY RANGE (created_at) with monthly partitions,
-- sub-partitioned by district_id hash for multi-district deployments.
