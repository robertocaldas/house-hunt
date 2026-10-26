#!/usr/bin/env python3
"""Daily house search near the Kristin School bus routes.

Source: realestate.co.nz public search API.
Filters: 4+ bedrooms, search price $900k-$1.5M, within 3 km (straight line)
of any Kristin bus stop, title not known to be non-freehold.

Writes data/houses.json and data/photos.json only when new houses are found
(or on a forced run). Removed listings stay (greyed out) for 30 days.
"""
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
API = "https://platform.realestate.co.nz/search/v1"
NZ = ZoneInfo("Pacific/Auckland")

RADIUS_KM = 3.0
PRICE_MIN, PRICE_MAX, BEDS_MIN = 900_000, 1_500_000, 4
REMOVED_KEEP_DAYS = 30
MAX_PHOTOS = 40
# realestate.co.nz title types: 1 Freehold, 2 Leasehold, 3 Cross Lease, 4 Unit Title, 5 Company
NON_FREEHOLD = {2, 3, 4, 5}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
    "Origin": "https://www.realestate.co.nz",
    "Referer": "https://www.realestate.co.nz/",
}


def get(path, params=None, tries=4):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params, safe="")
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


def load_stops():
    routes = json.load(open(os.path.join(DATA, "routes.json")))
    stops = []
    for r in routes:
        for g in ("am", "pm"):
            for name, lat, lng in r[g]:
                stops.append((lat, lng, r["code"], clean_stop(name)))
    return stops


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
    # (top, left, bottom, right) in degrees; top is the northern (less negative) edge
    return [((i1 + 1) * cell, a * cell, i0 * cell, (b + 1) * cell) for i0, i1, a, b in merged]


def search_all(boxes):
    found = {}
    for top, left, bot, right in boxes:
        off = 0
        while True:
            j = get("/listings", {
                "filter[category][]": "res_sale",
                "filter[saleMin]": PRICE_MIN,
                "filter[saleMax]": PRICE_MAX,
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
            time.sleep(0.4)
        time.sleep(0.4)
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
    suburb = ad.get("suburb-display") or ad.get("suburb") or ""
    disp = ad.get("display-address") or ad.get("full-address") or suburb
    street = disp
    if suburb and street.endswith(", " + suburb):
        street = street[: -(len(suburb) + 2)]
    dist, stop = nearest(lat, lng, stops)
    method, when = sale_method(a)
    url = a.get("website-full-url") or ("https://www.realestate.co.nz" + (a.get("website-slug") or f"/{d['id']}"))
    return {
        "id": d["id"],
        "addr": street,
        "sub": suburb,
        "full": ad.get("full-address") or disp,
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


def enrich(rec):
    """Title type, floor area, photos and price estimate for a new listing."""
    tt = None
    try:
        x = get(f"/listings/{rec['id']}")["data"]["attributes"]
        tt = x.get("title-type")
        rec["floor"] = x.get("floor-area") or rec["floor"]
        ph = [p["md5"] for p in sorted(x.get("photos") or [], key=lambda p: p.get("order", 0))]
        if ph:
            rec["_photos"] = ph
    except Exception as e:  # noqa: BLE001
        print("detail failed", rec["id"], e, file=sys.stderr)
    rec["freehold"] = True if tt == 1 else None
    if rec.get("_sid"):
        try:
            p = get(f"/properties/{rec['_sid']}")
            ev = ((p.get("data") or {}).get("attributes") or {}).get("estimated-value")
            if ev and ev.get("value-mid"):
                rec["est"] = [ev.get("value-low"), ev.get("value-mid"), ev.get("value-high"), ev.get("confidence-rating")]
        except Exception as e:  # noqa: BLE001
            print("estimate failed", rec["id"], e, file=sys.stderr)
    time.sleep(0.3)
    return tt


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

    hpath, ppath = os.path.join(DATA, "houses.json"), os.path.join(DATA, "photos.json")
    prev = json.load(open(hpath)) if os.path.exists(hpath) else {"houses": [], "excluded": {}}
    if event == "schedule" and prev.get("updated") == today and prev.get("updatedAt", "")[11:13] >= "20":
        print("Skipping: tonight's search already found new houses")
        set_output(changed="false", new_count=0, date=today)
        return
    photos = json.load(open(ppath)) if os.path.exists(ppath) else {}
    known = {h["id"]: h for h in prev["houses"]}
    excluded = prev.get("excluded", {})

    stops = load_stops()
    boxes = search_boxes(stops)
    print(f"{len(boxes)} search boxes")
    found = search_all(boxes)
    print(f"{len(found)} listings in boxes")

    current = {}
    for lid, d in found.items():
        rec = base_record(d, stops)
        if rec["lat"] and rec["dist"] <= RADIUS_KM and lid not in excluded:
            current[lid] = rec
    print(f"{len(current)} within {RADIUS_KM} km")

    added = []
    for lid, rec in current.items():
        if lid in known:
            continue
        tt = enrich(rec)
        if tt in NON_FREEHOLD:
            excluded[lid] = tt
            continue
        rec["first"] = today
        added.append(lid)
    print(f"{len(added)} new houses")

    if not added and not force:
        set_output(changed="false", new_count=0, date=today)
        return

    houses = []
    for lid, h in known.items():
        if lid in current:
            cur = current[lid]
            for k in ("addr", "sub", "full", "lat", "lng", "beds", "baths", "land", "type", "price",
                      "priceNum", "method", "when", "url", "dist", "route", "stop", "listed"):
                h[k] = cur[k]
            h["floor"] = cur["floor"] or h.get("floor", 0)
            h["removed"] = None
            h["last"] = today
            if cur["_photos"]:
                photos[lid] = cur["_photos"][:MAX_PHOTOS]
        elif not h.get("removed"):
            h["removed"] = today
        if h.get("removed"):
            age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(h["removed"])).days
            if age > REMOVED_KEEP_DAYS:
                photos.pop(lid, None)
                continue
        houses.append(h)
    for lid in added:
        rec = current[lid]
        photos[lid] = rec.pop("_photos")[:MAX_PHOTOS]
        rec.pop("_sid", None)
        rec["last"] = today
        rec["removed"] = None
        houses.append(rec)
    for h in houses:
        h.pop("_photos", None)
        h.pop("_sid", None)

    keep = {h["id"] for h in houses}
    photos = {k: v for k, v in photos.items() if k in keep}
    houses.sort(key=lambda h: (h.get("removed") is not None, h["sub"], h["addr"]))
    active = sum(1 for h in houses if not h.get("removed"))
    new_today = sum(1 for h in houses if h.get("first") == today and not h.get("removed"))
    out = {
        "updated": today,
        "updatedAt": now.isoformat(timespec="minutes"),
        "newCount": new_today,
        "active": active,
        "filters": {"priceMin": PRICE_MIN, "priceMax": PRICE_MAX, "bedsMin": BEDS_MIN, "radiusKm": RADIUS_KM},
        "houses": houses,
        "excluded": excluded,
    }
    with open(hpath, "w") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
    with open(ppath, "w") as f:
        json.dump(photos, f, separators=(",", ":"))
    set_output(changed="true", new_count=new_today, date=today)


if __name__ == "__main__":
    main()
