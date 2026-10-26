# Kristin Bus Homes

Houses for sale within 3 km driving distance of any Kristin School morning bus stop.

- **Site:** https://robertocaldas.github.io/house-hunt/
- **Filters:** 4+ bedrooms; no townhouses (apartments stay); within 3 km driving distance (OSRM, cached in `data/roaddist.json`) of any morning (AM) stop (Kristin School counts as a stop); title freehold or not stated (leasehold, cross lease, unit title and company titles are excluded); price $900k–$1.5M, checked against the advertised price, else the realestate.co.nz estimate (confidence 3+), else the council valuation. Listings with none of these are left out.
- **Source:** realestate.co.nz search API. Bus routes and stops from Kristin's RollCall map (`data/routes.json`, 2026 routes).
- **Schedule:** `.github/workflows/update.yml` runs daily at 8pm NZ time. Data is only committed when new houses appear. Off-market houses stay greyed out for 30 days.
- **Suburb trend:** median sale price, last 12 months vs the 12 before (4+ bedroom sales when 10+ each period, else all sales); within ±2% = flat (`data/suburbs.json`).
- **Routes:** road paths from OSRM (`data/route_paths.json`); dashed = the non-stop run to school.
- **Map colours:** light green = new in the latest update, blue = seen before, gold = favourite (saved per device), grey = off market.

Run locally: `python scraper/update.py --force`
