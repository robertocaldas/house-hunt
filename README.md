# Kristin Bus Homes

Houses for sale within 3 km driving distance of any Kristin School morning bus stop.

- **Site:** https://robertocaldas.github.io/house-hunt/
- **Filters:** 4+ bedrooms; 2+ bathrooms and 160 m²+ floor area (house, not land), kept when the listing doesn't say; no townhouses (apartments stay); within 3 km driving distance (OSRM, cached in `data/roaddist.json`) of any morning (AM) stop (Kristin School counts as a stop); title freehold or not stated (leasehold, cross lease, unit title and company titles are excluded); price $900k–$1.5M, checked against the advertised price, else the realestate.co.nz estimate (confidence 3+), else the council valuation. Listings with none of these are left out.
- **Source:** realestate.co.nz search API. Bus routes and stops from Kristin's RollCall map (`data/routes.json`, 2026 routes).
- **Schedule:** `.github/workflows/update.yml` has evening slots from 8:13pm NZ; GitHub often starts scheduled jobs late or skips them, so the first slot that runs after 8pm does the search and the rest skip (state in `data/lastrun.json`, Actions cache). A missed night is caught up by the next slot that runs. Data is only committed when new houses appear. Off-market houses stay greyed out for 30 days.
- **Suburb trend:** median sale price, last 12 months vs the 12 before (4+ bedroom sales when 10+ each period, else all sales); within ±2% = flat (`data/suburbs.json`).
- **House box checklist:** freehold title, 2+ bathrooms, 160 m²+ floor area — green tick when confirmed, red ? when not stated.
- **Commute to AIA (74 Taharoto Rd):** weekday 8am. Drive without traffic from OSRM; with 8am traffic from TomTom when the `TOMTOM_API_KEY` secret is set (free key, 20k requests/month); public transport from [Transitous](https://transitous.org/sources/) (Auckland Transport timetables), at most 250 requests a day. Cached in `data/commute.json` (Actions cache).
- **Sold prices:** houses that leave the list are checked nightly; sold ones stay greyed out for 60 days and show the sale price once realestate.co.nz has it (`data/sold.json`).
- **Routes:** road paths from OSRM (`data/route_paths.json`); dashed = the non-stop run to school.
- **Map colours:** light green = new in the latest update, blue = seen before, gold = favourite, grey = off market or trashed. Favourites and trash are saved per device; trashed houses stay greyed out for 30 days, then disappear.

Run locally: `python scraper/update.py --force`
