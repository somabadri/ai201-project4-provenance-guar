import os
import re
import uuid
import json
import math
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from groq import Groq

load_dotenv()

app = Flask(__name__)
limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://")
client = Groq(api_key=os.environ["GROQ_API_KEY"])

# --- DB setup ---

def get_db():
    conn = sqlite3.connect("audit.db")
    conn.row_factory = sqlite3.Row
    return conn

with get_db() as db:
    db.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id  TEXT,
            creator_id  TEXT,
            timestamp   TEXT,
            attribution TEXT,
            confidence  REAL,
            llm_score   REAL,
            stylo_score REAL,
            status      TEXT DEFAULT 'classified',
            appeal_reason TEXT
        )
    """)

# --- Signal 1: Groq LLM ---

def groq_score(text):
    """Returns float 0-1 where 1.0 = high confidence AI-generated."""
    prompt = (
        "Analyze the text below. Estimate the probability it was AI-generated vs human-written.\n"
        "Reply with ONLY valid JSON: {\"ai_probability\": <0.0 to 1.0>}\n\n"
        f"Text:\n{text}"
    )
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=64,
    )
    raw = response.choices[0].message.content.strip()
    if "```" in raw:
        raw = raw.split("```")[1].lstrip("json").strip()
    return max(0.0, min(1.0, float(json.loads(raw)["ai_probability"])))

# --- Signal 2: Stylometrics ---

def stylo_score(text):
    """Returns float 0-1 where 1.0 = statistically AI-like."""
    words = text.split()
    if len(words) < 20:
        return 0.5  # too short for reliable stats

    # Sentence length variance — low variance is AI-like
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    if len(sentences) > 1:
        lengths = [len(s.split()) for s in sentences]
        mean = sum(lengths) / len(lengths)
        std = math.sqrt(sum((l - mean) ** 2 for l in lengths) / len(lengths))
        variance_score = 1.0 - min(std / 15.0, 1.0)  # low std → high score
    else:
        variance_score = 0.5

    # Type-token ratio — low vocabulary diversity is AI-like
    unique = len(set(w.lower().strip('.,!?;:') for w in words))
    ttr = unique / len(words)
    ttr_score = 1.0 - min(ttr / 0.9, 1.0)  # low TTR → high score

    # Punctuation density — low density is AI-like
    punct = sum(1 for c in text if c in '.,!?;:—–-()[]"\' ')
    density = punct / len(text)
    punct_score = 1.0 - min(density / 0.25, 1.0)  # low density → high score

    return round((variance_score + ttr_score + punct_score) / 3, 4)

# --- Confidence + Label ---

def confidence_score(llm, stylo):
    return round(0.6 * llm + 0.4 * stylo, 4)

def make_label(confidence):
    pct = f"{confidence:.0%}"
    if confidence >= 0.80:
        return "ai", f"This work shows strong indicators of AI generation ({pct}). If you created this yourself, you can submit an appeal."
    elif confidence <= 0.30:
        return "human", f"This work shows strong indicators of human authorship ({pct}). No action required."
    else:
        return "uncertain", f"Our system isn't certain about the origin of this work ({pct}). This does not mean it is AI-generated. You may submit an appeal for the record."

# --- Routes ---

@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute; 50 per hour")
def submit():
    data = request.get_json(force=True) or {}
    text = data.get("text", "").strip()
    creator_id = data.get("creator_id", "anonymous")

    if not text:
        return jsonify({"error": "text is required"}), 400

    content_id = str(uuid.uuid4())
    llm = round(groq_score(text), 4)
    stylo = stylo_score(text)
    confidence = confidence_score(llm, stylo)
    attribution, label = make_label(confidence)

    with get_db() as db:
        db.execute(
            "INSERT INTO audit_log (content_id, creator_id, timestamp, attribution, confidence, llm_score, stylo_score) VALUES (?,?,?,?,?,?,?)",
            (content_id, creator_id, datetime.now(timezone.utc).isoformat(), attribution, confidence, llm, stylo),
        )

    return jsonify({
        "content_id": content_id,
        "attribution": attribution,
        "confidence": confidence,
        "llm_score": llm,
        "stylo_score": stylo,
        "label": label,
    })


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(force=True) or {}
    content_id = data.get("content_id", "").strip()
    reason = data.get("creator_reasoning", "").strip()

    if not content_id or not reason:
        return jsonify({"error": "content_id and creator_reasoning are required"}), 400

    with get_db() as db:
        row = db.execute("SELECT id FROM audit_log WHERE content_id = ?", (content_id,)).fetchone()
        if not row:
            return jsonify({"error": "content_id not found"}), 404
        db.execute(
            "UPDATE audit_log SET status = 'under_review', appeal_reason = ? WHERE content_id = ?",
            (reason, content_id),
        )

    return jsonify({
        "appeal_id": str(uuid.uuid4()),
        "content_id": content_id,
        "status": "under_review",
        "message": "Your appeal has been logged and the content is now under review.",
    })


@app.route("/status/<content_id>")
def status(content_id):
    with get_db() as db:
        row = db.execute("SELECT * FROM audit_log WHERE content_id = ?", (content_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))


@app.route("/log")
def log():
    limit = request.args.get("limit", 20, type=int)
    with get_db() as db:
        rows = db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return jsonify({"entries": [dict(r) for r in rows]})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
