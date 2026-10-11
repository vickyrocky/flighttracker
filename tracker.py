"""Hourly flight price tracker: Google Flights -> Telegram alerts.

Usage:
    python tracker.py               # check prices, send summary and deal alerts
    python tracker.py --dry-run     # check prices, print only, send nothing
    python tracker.py --test-notify # send a test Telegram message and exit
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fast_flights import FlightQuery, Passengers, create_query, fetch_flights_html
from selectolax.lexbor import LexborHTMLParser

# ----------------------------- configuration -----------------------------
DATES = ["2026-11-03", "2026-11-04", "2026-11-05"]
CITIES = {
    "BLR": "Bangalore",
    "MAA": "Chennai",
    "IXR": "Ranchi",
    "BBI": "Bhubaneswar",
    "CCU": "Kolkata",
    "JRG": "Jharsuguda",
    "VNS": "Varanasi",
}
# (origin, destination, highest price in INR worth an alert)
ROUTES = [
    ("BLR", "IXR", 8000),
    ("MAA", "IXR", 8000),
    ("BLR", "BBI", 7000),
    ("BLR", "CCU", 7000),
    ("BLR", "JRG", 7000),
    ("BLR", "VNS", 7000),
]
SEND_HOURLY_SUMMARY = True  # every run, send the lowest fare on each route
MAX_STOPS = None  # None = any, 0 = non-stop only, 1 = at most one stop
MAX_LINES_PER_MESSAGE = 12
# --------------------------------------------------------------------------

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
IST = timezone(timedelta(hours=5, minutes=30))


def load_env_file():
    """Read KEY=VALUE lines from .env for local runs (GitHub uses secrets)."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def hhmm(value):
    # Google omits zero components: [8] is 08:00 and [None, 31] is 00:31.
    padded = [*(value or []), None, None]
    return f"{padded[0] or 0:02d}:{padded[1] or 0:02d}"


def parse(html):
    """Return priced itineraries from a Google Flights results page."""
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise RuntimeError("results data missing from page (blocked or layout changed)")
    payload = json.loads(script.text().split("data:", 1)[1].rsplit(",", 1)[0])

    results = []
    for section in (2, 3):  # 2 = "best flights", 3 = "other flights"
        if len(payload) <= section or not payload[section]:
            continue
        for entry in payload[section][0] or []:
            try:
                flight, price = entry[0], entry[1][0]
                if not price or len(price) < 2 or not price[1]:
                    continue  # Google lists some itineraries without a price
                segments = flight[2]
                results.append(
                    {
                        "price": int(price[1]),
                        "airlines": ", ".join(flight[1]),
                        "flight_no": "+".join(s[22][0] + s[22][1] for s in segments),
                        "stops": len(segments) - 1,
                        "via": [s[6] for s in segments[:-1]],
                        "depart": hhmm(flight[5]),
                        "arrive": hhmm(flight[8]),
                        "next_day": flight[7] != flight[4],
                        "minutes": flight[9],
                    }
                )
            except (IndexError, TypeError, KeyError):
                continue  # one malformed entry must not sink the whole route
    return results


def search(origin, dest, date):
    query = create_query(
        flights=[FlightQuery(date=date, from_airport=origin, to_airport=dest)],
        trip="one-way",
        seat="economy",
        passengers=Passengers(adults=1),
        language="en-IN",
        currency="INR",
        max_stops=MAX_STOPS,
    )
    last_error = None
    for attempt in range(3):
        try:
            return parse(fetch_flights_html(query)), query.url()
        except Exception as error:  # network hiccup, block page, bad JSON
            last_error = error
            time.sleep(5 * (attempt + 1))
    raise last_error


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with urllib.request.urlopen(url, data=data, timeout=30) as response:
        if not json.load(response).get("ok"):
            raise RuntimeError("Telegram rejected the message")


def short_day(date):
    return datetime.strptime(date, "%Y-%m-%d").strftime("%a %d %b")


def flight_line(f):
    stops = "non-stop" if f["stops"] == 0 else f"{f['stops']} stop via {'/'.join(f['via'])}"
    arrive = f["arrive"] + (" (+1 day)" if f["next_day"] else "")
    hours, minutes = divmod(f["minutes"] or 0, 60)
    return (f"{f['airlines']} {f['flight_no']}, {f['depart']} to {arrive}, "
            f"{hours}h {minutes:02d}m, {stops}")


def html_link(url, label):
    return f'<a href="{url.replace("&", "&amp;")}">{label}</a>'


def describe(deal):
    f = deal["flight"]
    was = f" (was ₹{deal['previous']:,})" if deal["previous"] else ""
    return (
        f"<b>₹{f['price']:,}</b>{was} {deal['route']}, {short_day(deal['date'])}\n"
        f"{flight_line(f)}\n"
        f"{html_link(deal['url'], 'Open in Google Flights')}"
    )


def summarize(route_results):
    """One block per route: the lowest fare, plus the low for each other date."""
    blocks = []
    for origin, dest, limit in ROUTES:
        by_date = route_results[(origin, dest)]
        priced = {d: r for d, r in by_date.items() if isinstance(r, tuple)}
        failed = [d for d, r in by_date.items() if r == "failed"]
        lines = [f"<b>{CITIES[origin]} to {CITIES[dest]}</b> (limit ₹{limit:,})"]
        if priced:
            best_date = min(priced, key=lambda d: priced[d][0]["price"])
            f, url = priced[best_date]
            mark = "✅ " if f["price"] <= limit else ""
            price = html_link(url, f"₹{f['price']:,}")
            lines.append(f"{mark}{price} on {short_day(best_date)}")
            lines.append(flight_line(f))
            others = [f"{short_day(d)[4:]} ₹{r[0]['price']:,}"
                      for d, r in priced.items() if d != best_date]
            if others:
                lines.append("Other dates: " + ", ".join(others))
        elif not failed:
            lines.append("No flights listed")
        if failed:
            lines.append("⚠️ Could not read: " + ", ".join(short_day(d)[4:] for d in failed))
        blocks.append("\n".join(lines))
    now = datetime.now(IST).strftime("%d %b, %I:%M %p")
    return f"📊 <b>Lowest fares</b> as of {now}\n\n" + "\n\n".join(blocks)


def main():
    load_env_file()
    dry_run = "--dry-run" in sys.argv

    if "--test-notify" in sys.argv:
        send_telegram("✅ Flight tracker test message. Notifications are working.")
        print("Test message sent.")
        return 0

    today = datetime.now(IST).strftime("%Y-%m-%d")
    dates = [d for d in DATES if d >= today]
    if not dates:
        print("All travel dates have passed. Nothing to check.")
        return 0

    state = {"alerted": {}}
    if STATE_FILE.exists():
        saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        state["alerted"] = saved.get("alerted", {})
    alerted = state["alerted"]

    deals, errors, failed_routes, seen_keys, total = [], [], set(), set(), 0
    route_results = {}  # (origin, dest) -> {date: (cheapest flight, url) | "none" | "failed"}
    for origin, dest, limit in ROUTES:
        route = f"{CITIES[origin]} to {CITIES[dest]}"
        by_date = route_results[(origin, dest)] = {}
        for date in dates:
            total += 1
            label = f"{origin}-{dest} {date}"
            try:
                flights, url = search(origin, dest, date)
            except Exception as error:
                errors.append(f"{label}: {type(error).__name__}: {error}")
                failed_routes.add(f"{origin}-{dest}|{date}")
                by_date[date] = "failed"
                print(f"{label}: FAILED {type(error).__name__}: {error}")
                continue
            cheapest = min(flights, key=lambda f: f["price"], default=None)
            by_date[date] = (cheapest, url) if cheapest else "none"
            shown = f"₹{cheapest['price']:,}" if cheapest else "none"
            print(f"{label}: {len(flights)} priced flights, cheapest {shown} (limit ₹{limit:,})")
            for f in flights:
                if f["price"] > limit:
                    continue
                key = f"{origin}-{dest}|{date}|{f['flight_no']}"
                seen_keys.add(key)
                previous = alerted.get(key)
                if previous is None or f["price"] < previous:
                    deals.append({"key": key, "route": route, "date": date, "url": url,
                                  "flight": f, "previous": previous})
            time.sleep(2)  # be gentle with Google

    # Forget deals that are gone or back above the limit, so a later drop
    # alerts again. Routes that failed this run keep their memory.
    for key in list(alerted):
        if key not in seen_keys and key.rsplit("|", 1)[0] not in failed_routes:
            del alerted[key]

    deals.sort(key=lambda d: d["flight"]["price"])
    if deals:
        shown = deals[:MAX_LINES_PER_MESSAGE]
        text = "✈️ <b>Flight deals under your limits</b>\n\n" + "\n\n".join(map(describe, shown))
        if len(deals) > len(shown):
            text += f"\n\n...and {len(deals) - len(shown)} more under your limits."
        print(f"{len(deals)} new deal(s) found.")
        if dry_run:
            print(text)
        else:
            send_telegram(text)
            for deal in deals:  # only remember deals once the alert went out
                alerted[deal["key"]] = deal["flight"]["price"]
    else:
        print("No new deals under the limits.")

    if not dry_run:
        STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")

    if SEND_HOURLY_SUMMARY:
        summary = summarize(route_results)
        if dry_run:
            print(summary)
        else:
            send_telegram(summary)

    if errors:
        print(f"{len(errors)} of {total} searches failed this run.")
    return 1 if len(errors) == total else 0


if __name__ == "__main__":
    sys.exit(main())
