"""
McCurtis lead scanner. No API keys, no Reddit approval needed.
Reads public RSS/Atom feeds (Reddit + Google Alerts + anything in feeds.txt),
keeps posts where someone is asking for contractor-type work within ~60 miles
of Kalamazoo, and pushes NEW ones to the lead app. Safe to run on a schedule.

IMPORTANT: Reddit's rate limit gets worse the more you manually re-run this
in a short window. If you see a wall of "rate-limited" lines and 0 leads,
that's Reddit cooling you down from earlier testing -- wait 30-60 minutes
before running it by hand again. This version also has a circuit breaker:
after a few failures in a row it stops hammering Reddit for the rest of
that run instead of retrying every single source.
"""
import html
import os
import re
import sys
import time
import urllib.parse as up
import xml.etree.ElementTree as ET

import requests

API = os.environ.get("LEAD_API_URL", "https://lead-generator-e1w3.onrender.com/api/leads")
UA = "McCurtisLeadFinder/1.0 (by u/YOUR_REDDIT_USERNAME)"  # put your Reddit username in here

SUBS = ["kalamazoo", "Portage", "WesternMichigan"]

CITIES = [
    "Kalamazoo", "Portage", "Battle Creek", "Plainwell", "Otsego", "Allegan",
    "South Haven", "Paw Paw", "Three Rivers", "Sturgis", "Marshall", "Hastings",
    "Grand Rapids", "Holland", "Niles", "St. Joseph", "Benton Harbor",
    "Coldwater", "Vicksburg", "Dowagiac",
]
CITY_BATCH_SIZE = 5  # groups cities into fewer, bigger requests instead of one request per city

TRADES = [
    "general contractor", "contractor", "roofer", "roofing", "drywall",
    "home repair", "remodel", "remodeling", "renovation", "addition",
    "barn builder", "shed builder", "siding", "handyman", "painter", "painting",
    "deck", "basement finish", "kitchen remodel", "bathroom remodel",
]

ASK = r"(need|want|looking for|searching for|recommend|recommendation|anyone know|who (do|did|should)|quote|estimate|suggestion)"
TRADE_PATTERN = "(" + "|".join(re.escape(t) for t in TRADES) + ")"
NEED = [
    re.compile(ASK + r".{0,50}" + TRADE_PATTERN, re.I | re.S),
    re.compile(TRADE_PATTERN + r".{0,50}" + ASK, re.I | re.S),
]
ADS = re.compile(r"free estimate|licensed (and|&) insured|call (us|now)|we offer|our (team|crew)|book (now|today)|% off|\bwe (paint|build|remodel|are)\b|gumroad|shopify|etsy\.com", re.I)
NS = {"a": "http://www.w3.org/2005/Atom"}

MAX_RETRIES = 2
BASE_WAIT = 20
CONSECUTIVE_FAIL_LIMIT = 4  # circuit breaker: stop hitting Reddit for the rest of this run after this many failures in a row


def strip(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse(xml):
    """Yield (title, link, body, author) from an Atom feed."""
    for e in ET.fromstring(xml).findall("a:entry", NS):
        ln = e.find("a:link", NS)
        link = ln.get("href", "") if ln is not None else ""
        if "google.com/url" in link:  # Google Alerts wraps the real link
            link = up.parse_qs(up.urlparse(link).query).get("url", [link])[0]
        yield (strip(e.findtext("a:title", "", NS)), link,
               strip(e.findtext("a:content", "", NS)), e.findtext("a:author/a:name", "", NS))


def fetch(url, attempt=1):
    """Returns (text, hit_rate_limit_wall)."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        if r.status_code == 429 and attempt <= MAX_RETRIES:
            wait = int(r.headers.get("Retry-After", BASE_WAIT * (2 ** (attempt - 1))))
            print(f"  rate-limited, waiting {wait}s (attempt {attempt}/{MAX_RETRIES}): {url[:90]}")
            time.sleep(wait)
            return fetch(url, attempt + 1)
        if r.status_code != 200:
            print(f"  skipped ({r.status_code}): {url[:90]}")
            return "", (r.status_code == 429)
        return r.text, False
    except requests.RequestException as e:
        print(f"  error: {e}")
        return "", False


def sources():
    """(label, url, must_match_intent, require_city_list_or_None)"""
    q = up.quote_plus
    out = []
    combined_trades = " OR ".join(TRADES)

    for s in SUBS:
        out.append((f"Reddit r/{s}", f"https://www.reddit.com/r/{s}/new.rss?limit=100", True, None))
        out.append((f"Reddit r/{s}", f"https://www.reddit.com/r/{s}/search.rss?q={q(combined_trades)}&restrict_sr=1&sort=new&t=week", True, None))

    for i in range(0, len(CITIES), CITY_BATCH_SIZE):
        batch = CITIES[i:i + CITY_BATCH_SIZE]
        city_query = " OR ".join(batch)
        label = f"Reddit ({'/'.join(batch)})"
        out.append((label, f"https://www.reddit.com/search.rss?q={q(combined_trades + ' ' + city_query)}&sort=new&t=week", True, batch))

    if os.path.exists("feeds.txt"):
        for line in open("feeds.txt", encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                strict = line.startswith("match:")
                out.append(("Web alert", line.replace("match:", "", 1).strip(), strict, None))
    return out


def service_for(t):
    t = t.lower()
    for words, name in [
        (("barn",), "Barn construction"),
        (("shed",), "Shed construction"),
        (("roof",), "Roofing"),
        (("drywall",), "Drywall repair"),
        (("siding",), "Siding/exterior"),
        (("deck", "fence", "stain"), "Deck/Fence staining"),
        (("cabinet",), "Cabinet refinishing"),
        (("pressure wash",), "Pressure washing"),
        (("commercial", "office", "store"), "Commercial work"),
        (("basement",), "Basement finishing"),
        (("addition",), "Addition/room"),
        (("kitchen", "bathroom", "remodel", "renovation"), "Remodel/renovation"),
        (("exterior", "outside", "trim"), "Exterior work"),
        (("interior", "room", "bedroom", "walls"), "Interior work"),
        (("paint",), "Painting"),
        (("handyman", "home repair"), "Home repair"),
        (("contractor",), "General contractor"),
    ]:
        if any(w in t for w in words):
            return name
    return "Other"


def existing_links():
    for _ in range(3):
        try:
            leads = requests.get(API, timeout=120).json()["leads"]
            return {l.get("link") or l["desc"] for l in leads}
        except Exception as e:
            print("waiting on app...", e)
            time.sleep(10)
    sys.exit("Could not reach the lead app.")


def main():
    seen, added, consecutive_fails = existing_links(), 0, 0
    srcs = sources()
    print(f"Checking {len(srcs)} sources...")
    for label, url, strict, require_cities in srcs:
        if consecutive_fails >= CONSECUTIVE_FAIL_LIMIT and "Reddit" in label:
            print(f"  Reddit is rate-limiting hard right now -- skipping remaining Reddit "
                  f"sources for this run (will retry next scheduled run).")
            continue
        xml, hit_wall = fetch(url)
        time.sleep(4)
        consecutive_fails = consecutive_fails + 1 if hit_wall else 0
        if not xml:
            continue
        try:
            items = list(parse(xml))
        except ET.ParseError:
            continue
        for title, link, body, author in items:
            text = f"{title} {body}"
            if ADS.search(text):
                continue
            if strict and not any(p.search(text) for p in NEED):
                continue
            if require_cities and not any(c.lower() in text.lower() for c in require_cities):
                continue
            key = link or f'{label}:{title}'
            if key in seen:
                continue
            desc = f'{label}: "{title[:140]}"'
            name = ("u/" + author.strip("/").replace("u/", "")) if "Reddit" in label and author else up.urlparse(link).netloc or "Web"
            lead = {"name": name, "phone": "", "email": "", "zip": "", "service": service_for(text),
                    "urgency": "ASAP" if re.search(r"asap|urgent|this week|emergency", text, re.I) else "Flexible",
                    "desc": desc, "source": "reddit" if "Reddit" in label else "web", "link": link}
            try:
                requests.post(API, json=lead, timeout=60).raise_for_status()
                seen.add(key)
                added += 1
                print("  + new lead:", title[:70])
            except requests.RequestException as e:
                print("  push failed:", e)
    print(f"Done. {added} new lead(s).")


if __name__ == "__main__":
    main()
