"""
McCurtis lead scanner.

REDDIT REALITY CHECK (as of testing in Sept 2026): Reddit closed self-service
API applications in Nov 2025 and killed the old .json URL trick in May 2026.
There is no approval path left for a small personal project -- official,
higher-limit access is not something we can get here. Unauthenticated RSS
still works, but Reddit's tolerance for it is low (roughly ~10 requests per
minute per IP) and this IP has already been hit a lot during testing, so it
can take a while to "cool off." Because of that, Reddit is treated as a
BONUS source here, not the backbone:
  - Each run only checks a SMALL ROTATING SLICE of Reddit sources (not all
    of them), tracked in reddit_rotation.txt next to this script, so the
    full list still gets covered over several runs without hammering
    Reddit all at once.
  - Reddit requests do NOT retry-with-backoff anymore -- a 429 just means
    "skip it this run," so one rate-limited run doesn't turn into a
    multi-minute stall.
  - Uses old.reddit.com instead of www.reddit.com for RSS (untested by me
    whether it's actually treated differently by Reddit -- worth watching
    the log to see if it behaves better).
  - GitHub Actions should keep running with SKIP_REDDIT=1 -- it only
    checks feeds.txt (Google Alerts etc), which isn't rate-limited by us
    and doesn't care which IP asks.
  - The REAL backbone for steady lead flow is feeds.txt (Google Alerts)
    and the request form itself. Treat any Reddit leads as a bonus on top.

Set the environment variable SKIP_REDDIT=1 to skip all Reddit sources.
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
SKIP_REDDIT = os.environ.get("SKIP_REDDIT", "0") == "1"

REDDIT_BASE = "https://old.reddit.com"  # trying this instead of www.reddit.com
REDDIT_PER_RUN = 2  # only check this many Reddit sources per run -- keep this LOW
ROTATION_FILE = "reddit_rotation.txt"

SUBS = ["kzoo", "Portage"]  # NOTE: r/kalamazoo is dead/abandoned since 2015 (-> r/kzoo); r/WesternMichigan never existed
STATEWIDE_SUBS = ["Michigan"]  # real + active, but broad -- requires a city-name match to count (see all_reddit_sources)

CITIES = [
    "Kalamazoo", "Portage", "Battle Creek", "Plainwell", "Otsego", "Allegan",
    "South Haven", "Paw Paw", "Three Rivers", "Sturgis", "Marshall", "Hastings",
    "Grand Rapids", "Holland", "Niles", "St. Joseph", "Benton Harbor",
    "Coldwater", "Vicksburg", "Dowagiac",
]
CITY_BATCH_SIZE = 5

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


def strip(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse(xml):
    for e in ET.fromstring(xml).findall("a:entry", NS):
        ln = e.find("a:link", NS)
        link = ln.get("href", "") if ln is not None else ""
        if "google.com/url" in link:
            link = up.parse_qs(up.urlparse(link).query).get("url", [link])[0]
        yield (strip(e.findtext("a:title", "", NS)), link,
               strip(e.findtext("a:content", "", NS)), e.findtext("a:author/a:name", "", NS))


def fetch(url, retry_on_429):
    """retry_on_429=False for Reddit (skip immediately, don't dig the hole
    deeper); True for everything else (a couple of gentle retries is fine)."""
    max_retries = 2 if retry_on_429 else 0
    attempt = 1
    while True:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        except requests.RequestException as e:
            print(f"  error: {e}")
            return "", False
        if r.status_code == 429:
            if attempt <= max_retries:
                wait = int(r.headers.get("Retry-After", 20 * (2 ** (attempt - 1))))
                print(f"  rate-limited, waiting {wait}s (attempt {attempt}/{max_retries}): {url[:90]}")
                time.sleep(wait)
                attempt += 1
                continue
            print(f"  rate-limited -- skipping: {url[:90]}")
            return "", True
        if r.status_code != 200:
            print(f"  skipped ({r.status_code}): {url[:90]}")
            return "", False
        return r.text, False


def all_reddit_sources():
    """The full list of Reddit sources we'd LIKE to check. sources() only
    uses a small rotating slice of this per run -- see load/save rotation."""
    q = up.quote_plus
    out = []
    combined_trades = " OR ".join(TRADES)
    for s in SUBS:
        out.append((f"Reddit r/{s}", f"{REDDIT_BASE}/r/{s}/new.rss?limit=100", True, None))
        out.append((f"Reddit r/{s}", f"{REDDIT_BASE}/r/{s}/search.rss?q={q(combined_trades)}&restrict_sr=1&sort=new&t=week", True, None))
    for s in STATEWIDE_SUBS:
        # broad/statewide sub -- require a tracked city name in the text so Detroit/Ann Arbor etc. posts don't slip through
        out.append((f"Reddit r/{s}", f"{REDDIT_BASE}/r/{s}/new.rss?limit=100", True, CITIES))
        out.append((f"Reddit r/{s}", f"{REDDIT_BASE}/r/{s}/search.rss?q={q(combined_trades)}&restrict_sr=1&sort=new&t=week", True, CITIES))
    for i in range(0, len(CITIES), CITY_BATCH_SIZE):
        batch = CITIES[i:i + CITY_BATCH_SIZE]
        city_query = " OR ".join(batch)
        label = f"Reddit ({'/'.join(batch)})"
        out.append((label, f"{REDDIT_BASE}/search.rss?q={q(combined_trades + ' ' + city_query)}&sort=new&t=week", True, batch))
    return out


def load_rotation_index(total):
    try:
        with open(ROTATION_FILE, encoding="utf-8") as f:
            return int(f.read().strip()) % total
    except (FileNotFoundError, ValueError):
        return 0


def save_rotation_index(idx, total):
    try:
        with open(ROTATION_FILE, "w", encoding="utf-8") as f:
            f.write(str(idx % total))
    except OSError as e:
        print(f"  couldn't save rotation position: {e}")


def next_reddit_slice():
    reddit_all = all_reddit_sources()
    total = len(reddit_all)
    start = load_rotation_index(total)
    slice_ = [reddit_all[(start + i) % total] for i in range(min(REDDIT_PER_RUN, total))]
    save_rotation_index(start + REDDIT_PER_RUN, total)
    return slice_


def sources():
    """(label, url, must_match_intent, require_city_list_or_None, is_reddit)"""
    out = []
    if not SKIP_REDDIT:
        for label, url, strict, require_cities in next_reddit_slice():
            out.append((label, url, strict, require_cities, True))
    else:
        print("SKIP_REDDIT is set -- only checking non-Reddit feeds this run.")

    if os.path.exists("feeds.txt"):
        for line in open("feeds.txt", encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                strict = line.startswith("match:")
                out.append(("Web alert", line.replace("match:", "", 1).strip(), strict, None, False))
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
    seen, added = existing_links(), 0
    srcs = sources()
    print(f"Checking {len(srcs)} sources...")
    if not srcs:
        print("No sources to check (SKIP_REDDIT is on and feeds.txt is empty -- add some Google Alert feeds to feeds.txt).")
        return
    for label, url, strict, require_cities, is_reddit in srcs:
        print(f"  checking: {url}")
        xml, hit_wall = fetch(url, retry_on_429=not is_reddit)
        time.sleep(15 if is_reddit else 2)
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
