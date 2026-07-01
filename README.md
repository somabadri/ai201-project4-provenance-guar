# Provenance Guard

A Flask API that classifies text-based creative content as AI-generated or human-written, returns a transparency label, and handles creator appeals.

---

## Architecture

A submission flows through two independent detection signals, a confidence combiner, a label generator, and the audit log before returning a response.

```
POST /submit
    │
    ├─────────────────────────┐
    ▼                         ▼
[Groq LLM Signal]    [Stylometric Signal]
  llm_score (0–1)      stylo_score (0–1)
    └──────────┬───────────┘
               ▼
      [Confidence Scorer]
       0.6×llm + 0.4×stylo
               │
               ▼
       [Label Generator]        POST /appeal
        ≥0.80 → AI                   │
        ≤0.30 → human                ▼
        else  → uncertain   [Update status +
               │              log appeal reason]
               ▼
       [Audit Log] → SQLite
               │
               ▼
          JSON response
```

---

## Detection Signals

### Signal 1 — Groq LLM (`llm_score`)
Sends the text to `llama-3.3-70b-versatile` asking for an `ai_probability` score. Captures holistic patterns: unnaturally smooth prose, absence of personal voice, over-structured argumentation. Gets 60% weight because it understands meaning, not just surface statistics.

**Blind spot:** heavily edited AI output; texts under ~50 words where there isn't enough for the model to form a view.

### Signal 2 — Stylometrics (`stylo_score`)
Pure Python. Combines three sub-metrics, each normalized 0–1:
- **Sentence length variance** — AI text clusters around a mean; human writing is more erratic
- **Type-token ratio** — vocabulary diversity relative to word count; AI reuses tokens more evenly
- **Punctuation density** — AI output uses less expressive punctuation (dashes, ellipses, exclamation)

Gets 40% weight because it's structurally independent of the LLM signal — it can corroborate or push back on the LLM's judgment without repeating it.

**Blind spot:** academic and minimalist writing is intentionally uniform and scores AI-like regardless of origin.

### Why this pairing
One signal is semantic, one is structural. They can disagree — and when they do, the combined score stays in the uncertain band rather than forcing a false binary verdict.

---

## Confidence Scoring

```
confidence = 0.6 × llm_score + 0.4 × stylo_score
```

| confidence | label variant |
|---|---|
| ≥ 0.80 | high-confidence AI |
| 0.31 – 0.79 | uncertain |
| ≤ 0.30 | high-confidence human |

The thresholds are asymmetric: the system requires stronger evidence to label something AI-generated (≥ 0.80) than to label it human (≤ 0.30). This reflects the higher cost of a false positive — mislabeling a human creator's work is worse than missing an AI submission.

### Example submissions

**High-confidence result** (clearly AI-sounding paragraph):
```json
{
  "llm_score": 0.80,
  "stylo_score": 0.35,
  "confidence": 0.62,
  "attribution": "uncertain"
}
```

**Low-confidence result** (casual human writing):
```json
{
  "llm_score": 0.20,
  "stylo_score": 0.26,
  "confidence": 0.23,
  "attribution": "human"
}
```

The 0.39-point gap between these two inputs produces meaningfully different labels and attributions. A score of 0.62 surfaces the uncertain label and invites appeal; 0.23 gives the creator a clean pass.

---

## Transparency Labels

**High-confidence AI** (confidence ≥ 0.80):
> "This work shows strong indicators of AI generation (XX%). If you created this yourself, you can submit an appeal."

**High-confidence human** (confidence ≤ 0.30):
> "This work shows strong indicators of human authorship (XX%). No action required."

**Uncertain** (0.31–0.79):
> "Our system isn't certain about the origin of this work (XX%). This does not mean it is AI-generated. You may submit an appeal for the record."

The percentage shown is the combined confidence score rounded to the nearest whole number.

---

## Appeals Workflow

Any creator can contest a classification via `POST /appeal`:

```bash
curl -s -X POST http://localhost:5001/appeal \
  -H "Content-Type: application/json" \
  -d '{
    "content_id": "<id from /submit>",
    "creator_reasoning": "I wrote this myself over several drafts."
  }'
```

On receipt the system:
1. Looks up the original record (404 if not found)
2. Sets `status` → `under_review`
3. Writes the `appeal_reason` to the audit log entry

A reviewer opening `GET /log` sees the original scores, attribution, and the creator's reasoning alongside the updated status. Automated re-classification does not happen.

---

## Rate Limiting

Applied to `POST /submit`: **10 requests per minute, 50 per hour**.

**Reasoning:** A working writer submitting their own pieces might post 2–3 times in a session. 10/minute allows a burst for legitimate testing while making it impractical to flood the system — a script sending 100 probes to map the classifier's decision boundary would be throttled within the first minute. The 50/hour ceiling prevents sustained abuse across a session.

**Rate limit evidence** — 12 rapid requests, 10/min limit:
```
200
200
200
200
200
200
200
200
429
429
429
429
```

---

## Audit Log

Every submission writes a structured entry. Every appeal updates the same entry in place.

Sample `GET /log` output:
```json
{
  "entries": [
    {
      "id": 1,
      "content_id": "b2e7bacc-2c32-414c-ac51-debffa611918",
      "creator_id": "test-ai",
      "timestamp": "2026-07-01T04:33:09.695930+00:00",
      "attribution": "uncertain",
      "confidence": 0.6212,
      "llm_score": 0.8,
      "stylo_score": 0.3529,
      "status": "under_review",
      "appeal_reason": "I wrote this myself. I am a non-native English speaker and my writing style may appear more formal than typical."
    },
    {
      "id": 2,
      "content_id": "6e22c16e-454e-466e-9c94-ac3f67c63115",
      "creator_id": "test-human",
      "timestamp": "2026-07-01T04:33:10.123456+00:00",
      "attribution": "human",
      "confidence": 0.225,
      "llm_score": 0.2,
      "stylo_score": 0.2625,
      "status": "classified",
      "appeal_reason": null
    },
    {
      "id": 3,
      "content_id": "ec31c4cd-3468-4090-86f9-687210f2210f",
      "creator_id": "test-user-2",
      "timestamp": "2026-07-01T04:33:28.857629+00:00",
      "attribution": "ai",
      "confidence": 0.8,
      "llm_score": 0.8,
      "stylo_score": 0.2625,
      "status": "classified",
      "appeal_reason": null
    }
  ]
}
```

---

## Known Limitations

**Non-native English writers** are the most likely false positive case. Writing that is grammatically careful, formally structured, and light on colloquial punctuation scores AI-like on both signals — the LLM may read it as "too polished," and the stylometric signal will flag the low variance. This is a property of the signals themselves, not a calibration issue: both signals were designed to detect the uniformity that AI produces, and careful non-native writing happens to share that property. The asymmetric threshold (0.80 required for an AI label) reduces the harm, but won't eliminate it.

**Short texts (< 80 words)** return unreliable stylometric scores. The signal falls back to 0.5 for texts under 20 words, but the 20–80 word range is still noisy — variance and TTR computed on a handful of sentences aren't statistically stable.

---

## Spec Reflection

**Where the spec helped:** defining the asymmetric thresholds before writing any code forced the label generator to be a deliberate design choice rather than an afterthought. When it came time to implement `make_label()`, the thresholds (≥ 0.80, ≤ 0.30) were already decided and the code was a direct translation.

**Where implementation diverged:** the spec called for the stylometric signal to independently capture "burstiness" (local clustering of rare words). In practice, burstiness on short creative texts produced noisy results that weren't meaningfully different from the TTR metric — it was measuring the same underlying property with more noise. It was dropped in favor of punctuation density, which turned out to be a cleaner separator between AI and human writing.

---

## AI Usage

**1. Flask skeleton and Groq signal (Milestone 3)**
Prompted Claude with the detection signals section and architecture diagram. It generated the Flask app structure and `groq_score()` function. The JSON-parsing logic it produced used `.strip("`").lstrip("json")` which silently returned an empty string when the model returned plain JSON without fences. Replaced with a conditional check: only strip fences if `"```"` is actually present in the response.

**2. Stylometric signal (Milestone 4)**
Prompted Claude with the stylometrics spec and asked for the three sub-metrics. The initial implementation normalized punctuation density against a ceiling of `0.12`, which caused most human texts to max out the score early. Adjusted the ceiling to `0.25` after inspecting per-metric scores on the four test inputs and noticing punctuation was contributing almost nothing to the human vs. AI separation.

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# add GROQ_API_KEY to .env
python app.py
```

App runs on `http://localhost:5001`.

## Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/submit` | Classify text, returns label + scores |
| `POST` | `/appeal` | Contest a classification |
| `GET` | `/status/<content_id>` | Check content status |
| `GET` | `/log` | View audit log |
