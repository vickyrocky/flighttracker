"""Hourly flight price tracker: Google Flights -> Telegram alerts.

Usage:
    python tracker.py               # check prices, alert on deals
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
ORIGIN = "BLR"
DATES = ["2026-11-03", "2026-11-04", "2026-11-05"]
# destination airport code -> (city name, highest price in INR worth an alert)
DESTINATIONS = {
    "IXR": ("Ranchi", 8000),
    "BBI": ("Bhubaneswar", 7000),
    "CCU": ("Kolkata", 7000),
    "JRG": ("Jharsuguda", 7000),
    "VNS": ("Varanasi", 7000),
}
MAX_STOPS = None  # None = any, 0 = non-stop only, 1 = at most one stop
MAX_LINES_PER_MESSAGE = 12
FAILED_RUNS_BEFORE_WARNING = 3
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


def search(dest, date):
    query = create_query(
        flights=[FlightQuery(date=date, from_airport=ORIGIN, to_airport=dest)],
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


def describe(deal):
    f = deal["flight"]
    stops = "non-stop" if f["stops"] == 0 else f"{f['stops']} stop via {'/'.join(f['via'])}"
    day = datetime.strptime(deal["date"], "%Y-%m-%d").strftime("%a %d %b")
    arrive = f["arrive"] + (" (+1 day)" if f["next_day"] else "")
    hours, minutes = divmod(f["minutes"] or 0, 60)
    was = f" (was ₹{deal['previous']:,})" if deal["previous"] else ""
    link = deal["url"].replace("&", "&amp;")
    return (
        f"<b>₹{f['price']:,}</b>{was} to <b>{deal['city']}</b>, {day}\n"
        f"{f['airlines']} {f['flight_no']}, {f['depart']} to {arrive}, "
        f"{hours}h {minutes:02d}m, {stops}\n"
        f'<a href="{link}">Open in Google Flights</a>'
    )


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

    state = {"alerted": {}, "failed_runs": 0}
    if STATE_FILE.exists():
        state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    alerted = state["alerted"]

    deals, errors, failed_routes, seen_keys, total = [], [], set(), set(), 0
    for dest, (city, limit) in DESTINATIONS.items():
        for date in dates:
            total += 1
            try:
                flights, url = search(dest, date)
            except Exception as error:
                errors.append(f"{dest} {date}: {type(error).__name__}: {error}")
                failed_routes.add(f"{dest}|{date}")
                print(f"{dest} {date}: FAILED {type(error).__name__}: {error}")
                continue
            cheapest = min((f["price"] for f in flights), default=None)
            shown = "none" if cheapest is None else f"₹{cheapest:,}"
            print(f"{dest} {date}: {len(flights)} priced flights, cheapest {shown} (limit ₹{limit:,})")
            for f in flights:
                if f["price"] > limit:
                    continue
                key = f"{dest}|{date}|{f['flight_no']}"
                seen_keys.add(key)
                previous = alerted.get(key)
                if previous is None or f["price"] < previous:
                    deals.append({"key": key, "city": city, "date": date, "url": url,
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
        text = "✈️ <b>Flight deals from Bangalore</b>\n\n" + "\n\n".join(map(describe, shown))
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

    all_failed = len(errors) == total
    state["failed_runs"] = state["failed_runs"] + 1 if all_failed else 0
    if errors:
        print(f"{len(errors)} of {total} searches failed this run.")
    if all_failed and state["failed_runs"] == FAILED_RUNS_BEFORE_WARNING and not dry_run:
        send_telegram(
            "⚠️ Flight tracker could not read prices for "
            f"{FAILED_RUNS_BEFORE_WARNING} runs in a row.\nLast error: {errors[0][:300]}"
        )

    if not dry_run:
        STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    return 1 if all_failed else 0


if __name__ == "__main__":
    sys.exit(main())
