"""
Scans public subreddits for posts where someone is asking for a
painter recommendation in the Kalamazoo area, and pushes matches
into the lead API as "reddit" sourced leads.

Reads PUBLIC posts only via Reddit's official API (praw) -- no
login-walled scraping, no ToS violation.

Setup (do this once):
    1. pip install -r requirements.txt --break-system-packages
    2. Create a Reddit API app at https://www.reddit.com/prefs/apps
       -> "create app" -> type: "script"
       -> copy the string under the app name (client_id) and the "secret"
    3. Fill in REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET / REDDIT_USER_AGENT below
    4. Set API_URL to your deployed Render URL + /api/leads
    5. Run: python reddit_scraper.py
"""

import re

import praw
import requests

# ==== FILL THESE IN ====
REDDIT_CLIENT_ID = "YOUR_CLIENT_ID"
REDDIT_CLIENT_SECRET = "YOUR_CLIENT_SECRET"
REDDIT_USER_AGENT = "kalamazoo-painting-lead-finder by u/YOUR_USERNAME"
API_URL = "http://localhost:5000/api/leads"  # change to https://YOUR-APP.onrender.com/api/leads once deployed
# ========================

SUBREDDITS = ["kalamazoo", "Portage", "WesternMichigan"]

NEED_PATTERNS = [
    r"\bneed (a|an|some)?\s*(good\s+)?paint(er|ing)?\b",
    r"\brecommend.*(paint|painter)",
    r"\blooking for.*(paint|painter)",
    r"\banyone know.*(paint|painter)",
    r"\bwho did your (paint|exterior|siding|cabinets?)\b",
    r"\bquotes? for.*(paint|repaint|staining)",
    r"\brepaint\b",
    r"\bstain(ed|ing)? (my |the )?(deck|fence)\b",
]

SEEN_IDS_FILE = "seen_posts.txt"


def load_seen():
    try:
        with open(SEEN_IDS_FILE) as f:
            return set(line.strip() for line in f)
    except FileNotFoundError:
        return set()


def mark_seen(post_id):
    with open(SEEN_IDS_FILE, "a") as f:
        f.write(post_id + "\n")


def matches_need(text):
    text = text.lower()
    return any(re.search(p, text) for p in NEED_PATTERNS)


def push_lead(post):
    data = {
        "name": f"u/{post.author}" if post.author else "Reddit user",
        "phone": "",
        "email": "",
        "zip": "",
        "service": "Interior painting",
        "urgency": "Flexible",
        "desc": f"Reddit post in r/{post.subreddit}: \"{post.title}\" -- {post.url}",
        "source": "reddit",
    }
    try:
        res = requests.post(API_URL, json=data, timeout=10)
        res.raise_for_status()
        print(f"  -> pushed lead: {post.title[:60]}")
    except requests.RequestException as e:
        print(f"  -> FAILED to push lead ({e}): {post.title[:60]}")


def run():
    reddit = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
    )

    seen = load_seen()

    for sub_name in SUBREDDITS:
        print(f"Scanning r/{sub_name}...")
        subreddit = reddit.subreddit(sub_name)
        for post in subreddit.new(limit=100):
            if post.id in seen:
                continue
            full_text = f"{post.title} {post.selftext}"
            if matches_need(full_text):
                print(f"Match: {post.title}")
                push_lead(post)
            mark_seen(post.id)
            seen.add(post.id)


if __name__ == "__main__":
    run()

# --- Running this on a schedule later ---
# Linux/Mac crontab (crontab -e), every 2 hours:
#   0 */2 * * * cd /path/to/leadapp && /usr/bin/python3 reddit_scraper.py >> scraper.log 2>&1
# Windows: use Task Scheduler to run this script every couple hours.
