"""
McCurtis lead scanner. No API keys, no Reddit approval needed.
Reads public RSS/Atom feeds (Reddit searches + Google Alerts + any feed you add
to feeds.txt), keeps posts where someone is asking for a painter, and pushes
NEW ones to the lead app. Safe to run as often as you like: it skips anything
already on the dashboard.
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
QUERIES = ["painter", "repaint", "exterior paint", "stain deck"]
SITEWIDE = ["painter Kalamazoo", "painting Portage Michigan", "paint contractor Kalamazoo County"]

PAINT = r"(paint|painter|painting|repaint|stain|staining)"
ASK = r"(need|want|looking for|searching for|recommend|recommendation|anyone know|who (do|did|should)|quote|estimate|suggestion)"
NEED = [re.compile(ASK + r".{0,80}" + PAINT, re.I | re.S), re.compile(PAINT + r".{0,80}" + ASK, re.I | re.S)]
ADS = re.compile(r"free estimate|licensed (and|&) insured|call (us|now)|we offer|our (team|crew)|book (now|today)|% off|\bwe (paint|are)\b", re.I)
NS = {"a": "http://www.w3.org/2005/Atom"}


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


def fetch(url):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        if r.status_code != 200:
            print(f"  skipped ({r.status_code}): {url[:90]}")
            return ""
        return r.text
    except requests.RequestException as e:
        print(f"  error: {e}")
        return ""


def sources():
    """(label, url, must_match_intent)"""
    q = up.quote_plus
    out = []
    for s in SUBS:
        out.append((f"Reddit r/{s}", f"https://www.reddit.com/r/{s}/new.rss?limit=100", True))
        for w in QUERIES:
            out.append((f"Reddit r/{s}", f"https://www.reddit.com/r/{s}/search.rss?q={q(w)}&restrict_sr=1&sort=new&t=week", True))
    for w in SITEWIDE:
        out.append(("Reddit", f"https://www.reddit.com/search.rss?q={q(w)}&sort=new&t=week", True))
    if os.path.exists("feeds.txt"):
        for line in open("feeds.txt", encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                strict = line.startswith("match:")
                out.append(("Web alert", line.replace("match:", "", 1).strip(), strict))
    return out


def service_for(t):
    t = t.lower()
    for words, name in [(("cabinet",), "Cabinet refinishing"), (("deck", "fence", "stain"), "Deck/Fence staining"),
                        (("pressure wash",), "Pressure washing"), (("commercial", "office", "store"), "Commercial painting"),
                        (("exterior", "siding", "outside", "trim"), "Exterior painting"),
                        (("interior", "room", "bedroom", "kitchen", "walls"), "Interior painting")]:
        if any(w in t for w in words):
            return name
    return "Other"


def existing_descs():
    for _ in range(3):  # Render free tier can take ~1 min to wake up
        try:
            return {l["desc"] for l in requests.get(API, timeout=120).json()["leads"]}
        except Exception as e:
            print("waiting on app...", e)
            time.sleep(10)
    sys.exit("Could not reach the lead app.")


def main():
    seen, added = existing_descs(), 0
    for label, url, strict in sources():
        xml = fetch(url)
        time.sleep(2)  # be polite to Reddit
        if not xml:
            continue
        try:
            items = list(parse(xml))
        except ET.ParseError:
            continue
        for title, link, body, author in items:
            text = f"{title} {body}"
            if ADS.search(text) or (strict and not any(p.search(text) for p in NEED)):
                continue
            desc = f'{label}: "{title[:140]}" - {link}'
            if desc in seen:
                continue
            name = ("u/" + author.strip("/").replace("u/", "")) if "Reddit" in label and author else up.urlparse(link).netloc or "Web"
            lead = {"name": name, "phone": "", "email": "", "zip": "", "service": service_for(text),
                    "urgency": "ASAP" if re.search(r"asap|urgent|this week|emergency", text, re.I) else "Flexible",
                    "desc": desc, "source": "reddit" if "Reddit" in label else "web"}
            try:
                requests.post(API, json=lead, timeout=60).raise_for_status()
                seen.add(desc)
                added += 1
                print("  + new lead:", title[:70])
            except requests.RequestException as e:
                print("  push failed:", e)
    print(f"Done. {added} new lead(s).")


if __name__ == "__main__":
    main()
