"""
Self-hosted lead generator backend for McCurtis Painting.
Flask + SQLite. No third-party services.

Run:
    pip install -r requirements.txt --break-system-packages
    python app.py

Serves:
    GET  /                       -> the form/dashboard (static/index.html)
    POST /api/leads              -> create a lead (used by the form AND the scraper)
    GET  /api/leads              -> list all leads (used by the dashboard)
    POST /api/leads/<id>/status  -> update a lead's status
"""

import sqlite3
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "leads.db"

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id TEXT PRIMARY KEY,
            name TEXT,
            phone TEXT,
            email TEXT,
            zip TEXT,
            service TEXT,
            urgency TEXT,
            desc TEXT,
            source TEXT,
            status TEXT DEFAULT 'new',
            created_at INTEGER
        )
    """)
    conn.commit()
    conn.close()


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/leads", methods=["GET"])
def list_leads():
    conn = get_db()
    rows = conn.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify({"leads": [dict(r) for r in rows]})


@app.route("/api/leads", methods=["POST"])
def create_lead():
    data = request.get_json(force=True, silent=True) or {}

    if not data.get("name") or not data.get("desc"):
        return jsonify({"ok": False, "error": "name and desc are required"}), 400

    lead_id = "lead_" + uuid.uuid4().hex[:10]
    conn = get_db()
    conn.execute(
        """INSERT INTO leads (id, name, phone, email, zip, service, urgency, desc, source, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            lead_id,
            data.get("name", ""),
            data.get("phone", ""),
            data.get("email", ""),
            data.get("zip", ""),
            data.get("service", ""),
            data.get("urgency", ""),
            data.get("desc", ""),
            data.get("source", "form"),
            "new",
            int(time.time() * 1000),
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "id": lead_id})


@app.route("/api/leads/<lead_id>/status", methods=["POST"])
def update_status(lead_id):
    data = request.get_json(force=True, silent=True) or {}
    new_status = data.get("status")
    if new_status not in ("new", "contacted", "quoted", "won", "lost"):
        return jsonify({"ok": False, "error": "invalid status"}), 400

    conn = get_db()
    conn.execute("UPDATE leads SET status = ? WHERE id = ?", (new_status, lead_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


init_db()  # runs on import too, so gunicorn (production) creates the table on startup

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
