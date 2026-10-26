#!/usr/bin/env python3
"""Daily house search near the Kristin School bus routes.

Source: realestate.co.nz public search API.
A house is kept when it has 4+ bedrooms, lies within 3 km (straight line) of any
Kristin bus stop (Kristin School itself is one of the stops), its title is not
known to be non-freehold, and its price is between $900k and $1.5M, where price is:
  1. the advertised price, if the listing shows one; otherwise
  2. the realestate.co.nz estimate (mid value); otherwise
  3. the council valuation (CV).
Listings with none of the three are left out.

data/houses.json and data/photos.json are written only when new houses are found
(or on a forced run). Houses that drop out stay greyed out for 30 days.
"""
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
API = "https://platform.realestate.co.nz/search/v1"
OSRM = "https://router.project-osrm.org/route/v1/driving/"
NZ = ZoneInfo("Pacific/Auckland")
SCHEMA = 2

RADIUS_KM = 3.0
PRICE_MIN, PRICE_MAX, BEDS_MIN = 900_000, 1_500_000, 4
MIN_EST_CONFIDENCE = 3          # realestate.co.nz rates estimates 1 (low) to 5 (high)
VALUATION_MAX_AGE_DAYS = 30
REMOVED_KEEP_DAYS = 30
MAX_PHOTOS = 40
WORKERS = 6
# realestate.co.nz title types: 1 Freehold, 2 Leasehold, 3 Cross Lease, 4 Unit Title, 5 Company
NON_FREEHOLD = {2, 3, 4, 5}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
    "Origin": "https://www.realestate.co.nz",
    "Referer": "https://www.realestate.co.nz/",
}


def fetch_json(url, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"GET failed {url}: {last}")


def get(path, params=None, tries=4):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params, safe="")
    return fetch_json(url, tries)


def load(name, default):
    p = os.path.join(DATA, name)
    return json.load(open(p)) if os.path.exists(p) else default


def save(name, obj):
    with open(os.path.join(DATA, name), "w") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)


def hav(a, b, c, d):
    t = math.radians
    dl, dn = t(c - a), t(d - b)
    h = math.sin(dl / 2) ** 2 + math.cos(t(a)) * math.cos(t(c)) * math.sin(dn / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def clean_stop(name):
    name = re.sub(r"\s*Bus departs.*$", "", name, flags=re.I)
    name = re.sub(r"\s*\((?:ID\s*)?\d{4}\)", "", name)
    name = re.sub(r"\s*\(no (?:AT )?bus stop\)", "", name, flags=re.I)
    name = re.sub(r"\s+PM$", "", name)
    name = re.sub(r"\s+\d{1,2}[.:]\d{2}\s*am$", "", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip()


def load_routes():
    return json.load(open(os.path.join(DATA, "routes.json")))


def load_stops(routes):
    stops = []
    for r in routes:
        for g in ("am", "pm"):
            for name, lat, lng in r[g]:
                stops.append((lat, lng, r["code"], clean_stop(name)))
    return stops


# ---------------------------------------------------------------- route paths

def encode_polyline(points, precision=5):
    factor, out, plat, plng = 10 ** precision, [], 0, 0
    for lat, lng in points:
        ilat, ilng = round(lat * factor), round(lng * factor)
        for v in (ilat - plat, ilng - plng):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plng = ilat, ilng
    return "".join(out)


def osrm_path(points):
    coords = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)
    j = fetch_json(f"{OSRM}{coords}?overview=full&geometries=geojson&continue_straight=false", tries=3)
    if j.get("code") != "Ok":
        raise RuntimeError(j.get("code"))
    return [(round(lat, 5), round(lng, 5)) for lng, lat in j["routes"][0]["geometry"]["coordinates"]]


def update_route_paths(routes):
    """Road-following AM paths: pickups, then the non-stop leg to Kristin."""
    key = hashlib.md5(json.dumps(routes, sort_keys=True).encode()).hexdigest()[:12]
    paths = load("route_paths.json", {})
    if paths.get("_key") == key and all(r["code"] in paths for r in routes):
        return
    out = {"_key": key}
    for r in routes:
        am = [(s[1], s[2]) for s in r["am"]]
        pickups = [p for s, p in zip(r["am"], am) if clean_stop(s[0]) != "Kristin School"]
        school = next((p for s, p in zip(r["am"], am) if clean_stop(s[0]) == "Kristin School"), None)
        try:
            entry = {"pickup": encode_polyline(osrm_path(pickups)) if len(pickups) > 1 else ""}
            if school and pickups:
                entry["express"] = encode_polyline(osrm_path([pickups[-1], school]))
            out[r["code"]] = entry
            print("route path", r["code"], len(entry["pickup"]), len(entry.get("express", "")))
        except Exception as e:  # noqa: BLE001
            print("route path failed", r["code"], e, file=sys.stderr)
        time.sleep(1.0)
    if any(k != "_key" for k in out):
        if len(out) - 1 < len(routes):
            out["_key"] = None  # retry missing routes next run
        save("route_paths.json", out)


NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
STREETISH = re.compile(r"^\d+[A-Za-z]?\s+\D+\b(Road|Rd|Street|St|Avenue|Ave|Drive|Dr|Place|Pl|Lane|Ln|Crescent|Cres|Terrace|"
                       r"Tce|Way|Grove|Highway|Hwy|Parade|Rise|Close|Court|Track|Loop|Parkway)$", re.I)


def stop_key(lat, lng):
    return f"{lat:.6f},{lng:.6f}"


def reverse_geocode(lat, lng):
    url = f"{NOMINATIM}?format=jsonv2&addressdetails=1&zoom=18&lat={lat:.6f}&lon={lng:.6f}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "kristin-bus-homes/1.0 (https://github.com/robertocaldas/house-hunt)",
        "Accept-Language": "en-NZ,en",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8")).get("address") or {}


def update_stop_addresses(routes, budget_s=900):
    """Street address for every stop (OpenStreetMap reverse geocoding, cached)."""
    addrs = load("stop_addresses.json", {})
    todo, seen = [], set()
    for r in routes:
        for g in ("am", "pm"):
            for name, lat, lng in r[g]:
                k = stop_key(lat, lng)
                if k not in addrs and k not in seen:
                    seen.add(k)
                    todo.append((k, clean_stop(name), lat, lng))
    if not todo:
        return
    print(f"geocoding {len(todo)} stops")
    start = time.time()
    for k, name, lat, lng in todo:
        if time.time() - start > budget_s:
            print("geocoding paused; continues next run")
            break
        try:
            a = reverse_geocode(lat, lng)
        except Exception as e:  # noqa: BLE001
            print("geocode failed", name, e, file=sys.stderr)
            time.sleep(2)
            continue
        street = name if STREETISH.match(name) else " ".join(x for x in (a.get("house_number"), a.get("road")) if x)
        area = a.get("suburb") or a.get("neighbourhood") or a.get("city_district") or a.get("hamlet") or ""
        town = a.get("city") or a.get("town") or a.get("village") or "Auckland"
        tail = " ".join(x for x in (town, a.get("postcode")) if x)
        parts = [street or name, area if area and area != town else "", tail]
        addrs[k] = ", ".join(x for x in parts if x)
        time.sleep(1.1)  # Nominatim usage policy: max 1 request per second
    save("stop_addresses.json", addrs)


# ---------------------------------------------------------------- listings

def search_boxes(stops, cell=0.03):
    """Non-overlapping rectangles covering every stop's radius."""
    dlat = RADIUS_KM / 111.0 + 0.003
    dlng = RADIUS_KM / (111.0 * math.cos(math.radians(36.8))) + 0.003
    cells = set()
    for la, ln, *_ in stops:
        for i in range(math.floor((la - dlat) / cell), math.floor((la + dlat) / cell) + 1):
            for j in range(math.floor((ln - dlng) / cell), math.floor((ln + dlng) / cell) + 1):
                cells.add((i, j))
    rows = {}
    for i, j in cells:
        rows.setdefault(i, []).append(j)
    runs = []
    for i, js in rows.items():
        js.sort()
        start = prev = js[0]
        for j in js[1:]:
            if j == prev + 1:
                prev = j
                continue
            runs.append((i, start, prev))
            start = prev = j
        runs.append((i, start, prev))
    runs.sort(key=lambda r: (r[1], r[2], r[0]))
    merged = []
    for i, a, b in runs:
        m = merged[-1] if merged else None
        if m and m[2] == a and m[3] == b and m[1] == i - 1:
            m[1] = i
        else:
            merged.append([i, i, a, b])
    return [((i1 + 1) * cell, a * cell, i0 * cell, (b + 1) * cell) for i0, i1, a, b in merged]


def search_all(boxes):
    """All 4+ bedroom listings in the boxes (no price pre-filter: price is decided below)."""
    found = {}
    for top, left, bot, right in boxes:
        off = 0
        while True:
            j = get("/listings", {
                "filter[category][]": "res_sale",
                "filter[bedroomsMin]": BEDS_MIN,
                "filter[geoBoundingBox]": f"{top:.4f},{left:.4f}|{bot:.4f},{right:.4f}",
                "page[offset]": off,
                "page[limit]": 100,
            })
            data = j.get("data") or []
            for d in data:
                found[d["id"]] = d
            off += len(data)
            if not data or off >= j["meta"]["totalResults"]:
                break
            time.sleep(0.3)
    return found


MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*([kKmM])?")


def parse_price(text):
    m = MONEY.search(text or "")
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    if unit == "k":
        v *= 1_000
    elif unit == "m":
        v *= 1_000_000
    return int(v) if v >= 10_000 else None


def sale_method(a):
    text = (a.get("price-display") or "").strip()
    if MONEY.search(text):
        label = MONEY.sub("", text)
        label = re.sub(r"\b(plus|including|incl\.?)\s+GST.*$", "", label, flags=re.I).strip(" -:")
        method = label or "Asking price"
    else:
        method = text or "Price on enquiry"
    when = a.get("auction-date") or a.get("tender-date") or a.get("deadline-date")
    return method, when


def nearest(lat, lng, stops):
    best = (1e9, None)
    for s in stops:
        d = hav(lat, lng, s[0], s[1])
        if d < best[0]:
            best = (d, s)
    return best


def base_record(d, stops):
    a = d["attributes"]
    ad = a.get("address") or {}
    lat, lng = float(ad.get("latitude") or 0), float(ad.get("longitude") or 0)
    suburb = (ad.get("suburb-display") or ad.get("suburb") or "").strip()
    disp = (ad.get("display-address") or ad.get("full-address") or suburb).strip()
    street = disp
    if suburb and street.endswith(", " + suburb):
        street = street[: -(len(suburb) + 2)]
    dist, stop = nearest(lat, lng, stops) if lat else (1e9, None)
    method, when = sale_method(a)
    url = a.get("website-full-url") or ("https://www.realestate.co.nz" + (a.get("website-slug") or f"/{d['id']}"))
    return {
        "id": d["id"],
        "addr": street.strip(),
        "sub": suburb,
        "full": (ad.get("full-address") or disp).strip(),
        "lat": round(lat, 6),
        "lng": round(lng, 6),
        "beds": a.get("bedroom-count") or 0,
        "baths": a.get("bathroom-count") or a.get("bathrooms-total-count") or 0,
        "floor": a.get("floor-area") or 0,
        "land": a.get("land-area") or 0,
        "type": a.get("listing-sub-type") or "",
        "price": (a.get("price-display") or "").strip(),
        "priceNum": parse_price(a.get("price-display")),
        "method": method,
        "when": when,
        "url": url,
        "dist": round(dist, 2),
        "route": stop[2] if stop else None,
        "stop": stop[3] if stop else None,
        "listed": (a.get("published-date") or "")[:10],
        "_sid": a.get("property-short-id"),
        "_photos": [p["md5"] for p in sorted(a.get("photos") or [], key=lambda p: p.get("order", 0))],
    }


def fetch_valuation(sid, today):
    """realestate.co.nz estimate and latest council valuation for a property."""
    v = {"e": None, "cv": None, "t": today}
    if not sid:
        return v
    try:
        attrs = (get(f"/properties/{sid}").get("data") or {}).get("attributes") or {}
    except Exception as e:  # noqa: BLE001
        print("valuation failed", sid, e, file=sys.stderr)
        v["t"] = None  # retry next run
        return v
    ev = attrs.get("estimated-value") or {}
    if ev.get("value-mid"):
        v["e"] = [ev.get("value-low"), ev.get("value-mid"), ev.get("value-high"), ev.get("confidence-rating")]
    evals = [c for c in (attrs.get("council-evaluations") or []) if c.get("capital-value")]
    if evals:
        c = max(evals, key=lambda c: c.get("current-valuation-date") or "")
        v["cv"] = [c["capital-value"], (c.get("current-valuation-date") or "")[:10]]
    elif attrs.get("capital-value"):
        v["cv"] = [attrs["capital-value"], (attrs.get("current-valuation-date") or "")[:10]]
    return v


def apply_value(rec, val):
    """Pick the price basis; return the value used for the price filter (or None)."""
    rec["est"], rec["cv"] = None, None
    if rec["priceNum"]:
        rec["basis"] = "price"
        return rec["priceNum"]
    e = (val or {}).get("e")
    if e and e[1] and (e[3] or 0) >= MIN_EST_CONFIDENCE:
        rec["basis"], rec["est"] = "est", e
        return e[1]
    cv = (val or {}).get("cv")
    if cv and cv[0]:
        rec["basis"], rec["cv"] = "cv", cv
        return cv[0]
    rec["basis"] = None
    return None


def fetch_detail(lid):
    try:
        x = get(f"/listings/{lid}")["data"]["attributes"]
        ph = [p["md5"] for p in sorted(x.get("photos") or [], key=lambda p: p.get("order", 0))]
        return {"tt": x.get("title-type"), "floor": x.get("floor-area") or 0, "photos": ph}
    except Exception as e:  # noqa: BLE001
        print("detail failed", lid, e, file=sys.stderr)
        return None


def set_output(**kv):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            for k, v in kv.items():
                f.write(f"{k}={v}\n")
    print(kv)


def main():
    now = dt.datetime.now(NZ)
    today = now.date().isoformat()
    event = os.environ.get("EVENT", "manual")
    force = event in ("push", "workflow_dispatch", "manual") or "--force" in sys.argv
    if event == "schedule" and now.hour < 20:
        print(f"Skipping: {now:%H:%M} NZ is before 8pm")
        set_output(changed="false", new_count=0, date=today)
        return

    prev = load("houses.json", {"houses": []})
    if event == "schedule" and prev.get("updated") == today and prev.get("updatedAt", "")[11:13] >= "20":
        print("Skipping: tonight's search already found new houses")
        set_output(changed="false", new_count=0, date=today)
        return
    rebuild = prev.get("schema") != SCHEMA  # filter rules changed: drop non-matching houses outright
    photos = load("photos.json", {})
    vals = load("valuations.json", {})
    titles = load("titles.json", {})
    for lid, tt in (prev.get("excluded") or {}).items():  # schema 1 kept title types here
        titles.setdefault(lid, tt)
    known = {h["id"]: h for h in prev["houses"]}

    routes = load_routes()
    stops = load_stops(routes)
    try:
        update_route_paths(routes)
    except Exception as e:  # noqa: BLE001
        print("route paths skipped:", e, file=sys.stderr)
    try:
        update_stop_addresses(routes)
    except Exception as e:  # noqa: BLE001
        print("stop addresses skipped:", e, file=sys.stderr)

    boxes = search_boxes(stops)
    found = search_all(boxes)
    print(f"{len(boxes)} boxes, {len(found)} listings with {BEDS_MIN}+ bedrooms")

    near = {}
    for lid, d in found.items():
        rec = base_record(d, stops)
        if rec["lat"] and rec["dist"] <= RADIUS_KM and rec["beds"] >= BEDS_MIN:
            near[lid] = rec
    print(f"{len(near)} within {RADIUS_KM} km of a stop")

    # Valuations for unpriced listings (cached; refreshed monthly).
    def stale(lid):
        v = vals.get(lid)
        if not v or not v.get("t"):
            return True
        return (dt.date.fromisoformat(today) - dt.date.fromisoformat(v["t"])).days > VALUATION_MAX_AGE_DAYS
    need = [lid for lid, r in near.items() if not r["priceNum"] and str(titles.get(lid)) not in ("2", "3", "4", "5") and stale(lid)]
    print(f"fetching {len(need)} valuations")
    with ThreadPoolExecutor(WORKERS) as ex:
        for lid, v in zip(need, ex.map(lambda l: fetch_valuation(near[l]["_sid"], today), need)):
            vals[lid] = v

    in_range = {}
    for lid, rec in near.items():
        value = apply_value(rec, vals.get(lid))
        if value is not None and PRICE_MIN <= value <= PRICE_MAX:
            in_range[lid] = rec
    print(f"{len(in_range)} priced ${PRICE_MIN:,}-${PRICE_MAX:,}")

    # Title type and photos for houses not seen before.
    need = [lid for lid in in_range if lid not in known and lid not in titles]
    print(f"fetching {len(need)} listing details")
    details = {}
    with ThreadPoolExecutor(WORKERS) as ex:
        for lid, det in zip(need, ex.map(fetch_detail, need)):
            if det:
                details[lid] = det
                titles[lid] = det["tt"]

    current = {}
    for lid, rec in in_range.items():
        tt = titles.get(lid)
        if tt in NON_FREEHOLD:
            continue
        if lid in known:
            rec["freehold"] = known[lid].get("freehold")
        else:
            rec["freehold"] = True if tt == 1 else None
        det = details.get(lid)
        if det:
            rec["floor"] = det["floor"] or rec["floor"]
            if det["photos"]:
                rec["_photos"] = det["photos"]
        current[lid] = rec

    added = [lid for lid in current if lid not in known]
    print(f"{len(current)} matching houses, {len(added)} new")

    save("valuations.json", {k: v for k, v in vals.items() if k in near})
    save("titles.json", {k: v for k, v in titles.items() if k in near})

    if not added and not force and not rebuild:
        set_output(changed="false", new_count=0, date=today)
        return

    houses = []
    for lid, h in known.items():
        if lid in current:
            cur = current[lid]
            cur["first"] = h.get("first", today)
            cur["last"] = today
            cur["removed"], cur["gone"] = None, None
            if not cur["floor"]:
                cur["floor"] = h.get("floor", 0)
            if cur["_photos"]:
                photos[lid] = cur["_photos"][:MAX_PHOTOS]
            houses.append(cur)
            continue
        if rebuild and lid in found:
            photos.pop(lid, None)  # still listed but outside the new rules: drop, don't grey out
            continue
        if not h.get("removed"):
            h["removed"] = today
            h["gone"] = "filter" if lid in found else "offmarket"
        age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(h["removed"])).days
        if age > REMOVED_KEEP_DAYS:
            photos.pop(lid, None)
            continue
        houses.append(h)
    for lid in added:
        rec = current[lid]
        rec["first"], rec["last"], rec["removed"], rec["gone"] = today, today, None, None
        photos[lid] = rec["_photos"][:MAX_PHOTOS]
        houses.append(rec)
    for h in houses:
        h.pop("_photos", None)
        h.pop("_sid", None)
        h.pop("excluded", None)

    keep = {h["id"] for h in houses}
    photos = {k: v for k, v in photos.items() if k in keep}
    houses.sort(key=lambda h: (h.get("removed") is not None, h["sub"], h["addr"]))
    active = sum(1 for h in houses if not h.get("removed"))
    new_today = sum(1 for h in houses if h.get("first") == today and not h.get("removed"))
    save("houses.json", {
        "schema": SCHEMA,
        "updated": today,
        "updatedAt": now.isoformat(timespec="minutes"),
        "newCount": new_today,
        "active": active,
        "filters": {"priceMin": PRICE_MIN, "priceMax": PRICE_MAX, "bedsMin": BEDS_MIN, "radiusKm": RADIUS_KM},
        "houses": houses,
    })
    save("photos.json", photos)
    set_output(changed="true", new_count=new_today, date=today)


if __name__ == "__main__":
    main()
