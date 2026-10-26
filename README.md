# Kristin Bus Homes

Houses for sale within 3 km (straight line) of any Kristin School bus stop.

- **Site:** https://robertocaldas.github.io/house-hunt/
- **Filters:** 4+ bedrooms, search price $900k–$1.5M, title freehold or not stated (leasehold, cross lease, unit title and company titles are excluded).
- **Source:** realestate.co.nz search API. Bus routes and stops from Kristin's RollCall map (`data/routes.json`, 2026 routes).
- **Schedule:** `.github/workflows/update.yml` runs daily at 8pm NZ time. Data is only committed when new houses appear. Off-market houses stay greyed out for 30 days.
- **Map colours:** light green = new in the latest update, blue = seen before, gold = favourite (saved per device), grey = off market.

Run locally: `python scraper/update.py --force`
