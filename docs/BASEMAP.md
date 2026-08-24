# The offline basemap

The dashboard renders a real map — roads, rivers, coastline, place names — with
**no network access at render time and no API key**. This document explains why,
and how to regenerate it for a different district.

## Why not Google Maps

It was considered and rejected for four reasons, in order of weight:

1. **It needs the internet at the moment we can least afford it.** SETU's whole
   architecture assumes the network is gone: `OFFLINE_MODE=true` is the default,
   CAP runs off `fixtures/`, and the verification checklist ends with *"unplug
   the Wi-Fi and repeat end to end."* Putting the most visible component of the
   product behind a live CDN call contradicts the pitch.
2. **The obvious workaround is forbidden.** Google Maps Platform Terms §3.2.4(a)
   prohibit caching or pre-downloading tiles, so "grab them beforehand" — which
   is exactly what we do below with OpenStreetMap — is not legally available.
3. **It is a rewrite, not an addition.** The dashboard is MapLibre GL. Google's
   SDK is a different API with no equivalent of the style expressions the
   severity heatmap depends on:
   `['interpolate', ['linear'], ['get','severity'], 0,0, 100,1]`.
4. **A key with billing attached is a demo-day failure mode.** Quota exhaustion
   or an expired key renders as a grey grid, silently.

## What we use instead

| | |
|---|---|
| Format | [PMTiles](https://protomaps.com/docs/pmtiles) v3 — a whole tile pyramid in **one file**, read via HTTP range requests |
| Data | OpenStreetMap, via the Protomaps daily planet build |
| Renderer | MapLibre GL (already in the stack) + `protomaps-themes-base` (dark) |
| Archive | `frontend-dashboard/public/basemap/ganjam.pmtiles` — **6.7 MB**, zoom 0–14 |
| Glyphs | `frontend-dashboard/public/basemap/fonts/` — **1.2 MB**, Latin + Devanagari + Odia |
| Licence | ODbL. Attribution is rendered in the map's bottom-left control. |
| Cost | none |

There is no tile server. nginx serves the archive as a static file and MapLibre
asks for byte ranges; `location /basemap/` in
[`nginx.conf`](../frontend-dashboard/nginx.conf) marks it `immutable` so a
`make reset` never re-downloads it.

**Degradation:** if the archive is missing or corrupt, the theme's `background`
layer still paints and every operational layer is our own GeoJSON — so the map
falls back to exactly the flat dark ground it had before, with all incidents,
units and dispatch lines intact. It logs a warning and carries on. This is
tested by renaming the file; nothing throws.

## Regenerating (another district, or fresher data)

Needs network **once**, at prep time — never at demo time. Do this days ahead.

### 1. Get the `pmtiles` CLI

Download the release for your platform from
<https://github.com/protomaps/go-pmtiles/releases>.

### 2. Find a live planet build

Builds are published daily and retained for a few weeks:

```bash
pmtiles show https://build.protomaps.com/YYYYMMDD.pmtiles
```

Walk back a day at a time until one resolves; a 404 means it has aged out.

### 3. Extract your district's bounding box

Ganjam, with a small margin around the boundary in
[`backend/seed/ganjam.py`](../backend/seed/ganjam.py):

```bash
pmtiles extract https://build.protomaps.com/YYYYMMDD.pmtiles \
  frontend-dashboard/public/basemap/ganjam.pmtiles \
  --bbox=84.10,18.95,85.35,20.15 \
  --maxzoom=14
```

Took ~26 seconds and transferred 7.0 MB for a 6.7 MB archive. `--maxzoom=14` is
street-level; 15 roughly triples the size for detail a DEOC does not use.

### 4. Glyphs, only if you change the label scripts

The five ranges in `public/basemap/fonts/` cover Latin, Devanagari (Hindi) and
Odia across three Noto Sans weights. A district needing another script wants the
matching range from
<https://github.com/protomaps/basemaps-assets/tree/main/fonts>, for each of
*Noto Sans Regular*, *Medium* and *Italic*:

| Range | Script |
|---|---|
| `0-255` | Latin basic |
| `256-511` | Latin extended-A |
| `2304-2559` | Devanagari |
| `2816-3071` | Odia |
| `8192-8447` | general punctuation |

The full set is 6.2 MB per font; shipping only what renders keeps it at 1.2 MB.

### 5. Rebuild

```bash
docker compose up -d --build dashboard
```

## Verifying it actually works

```bash
# range requests must return 206 — this is the entire mechanism
curl -s -D - -o /dev/null -H "Range: bytes=0-16383" \
  http://localhost:5173/basemap/ganjam.pmtiles | grep -i "206\|content-range"

# and the archive must be readable through nginx
pmtiles show http://localhost:5173/basemap/ganjam.pmtiles
```

## Known limitation

`protomaps-themes-base@4` is deprecated upstream in favour of
`@protomaps/basemaps@5`. We stay on v4 deliberately: the extracted archive
reports basemap schema **v4.15.2**, and its source layers (`earth`, `landcover`,
`landuse`, `roads`, `water`, `buildings`, `boundaries`, `pois`, `places`) match
the v4 theme exactly. Upgrading the theme without regenerating the archive
against a v5 build would silently render an empty map — the layers would simply
find no data. Change both together or neither.
