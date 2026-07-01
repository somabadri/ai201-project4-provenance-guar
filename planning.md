# Provenance Guard — Planning

---

## Detection Signals

**Signal 1 — Groq LLM (`llm_score`, float 0–1)**
Prompts `llama-3.3-70b-versatile` to rate how likely the text is AI-generated. Returns a parsed float. Captures holistic tone, stylistic smoothness, and semantic patterns. Blind spot: heavily edited AI output; texts under ~50 words.

**Signal 2 — Stylometrics (`stylo_score`, float 0–1)**
Pure Python. Computes:
- Sentence length variance (low = AI-like)
- Type-token ratio / vocabulary diversity (high repetition = AI-like)
- Punctuation density (low = AI-like)

Each sub-metric is normalized 0–1 and averaged into `stylo_score`. Blind spot: academic/minimalist writing scores AI-like; short texts have noisy statistics.

**Combining signals:**
```
confidence = 0.6 * llm_score + 0.4 * stylo_score
```
LLM gets higher weight because it understands context; stylometrics provides structural corroboration.

---

## Uncertainty Representation

| confidence | meaning | label variant |
|---|---|---|
| ≥ 0.80 | strong evidence of AI | high-confidence AI |
| 0.31 – 0.79 | ambiguous | uncertain |
| ≤ 0.30 | strong evidence of human | high-confidence human |

Thresholds are asymmetric by design: the system needs stronger evidence to label something AI-generated (0.80) than to label it human (0.30). This reflects the higher cost of a false positive. A score of 0.62 means "more AI-like than not, but not confidently — show the uncertain label and allow appeal."

---

## Transparency Labels

**High-confidence AI (confidence ≥ 0.80)**
> "This work shows strong indicators of AI generation (confidence: {score}%). It has been flagged for platform review. If you created this yourself, you can submit an appeal."

**High-confidence human (confidence ≤ 0.30)**
> "This work shows strong indicators of human authorship (confidence: {score}%). No action is required."

**Uncertain (0.31 – 0.79)**
> "Our system isn't certain about the origin of this work (confidence: {score}%). This does not mean it is AI-generated. If you created this yourself, no action is needed — but you may submit an appeal for the record."

`{score}` is displayed as a percentage rounded to the nearest whole number (e.g., 73%).

---

## Appeals Workflow

- **Who:** any creator, identified by `creator_id`
- **What they provide:** `content_id` + free-text `reason`
- **What the system does:**
  1. Looks up the original audit record by `content_id` (404 if not found)
  2. Sets `status` → `"under_review"`
  3. Writes an appeal record to the audit log: `appeal_id`, `content_id`, `reason`, timestamp
- **What a reviewer sees in the queue:** original `content_id`, original `confidence`, `label_variant`, creator's `reason`, and timestamps for both the original decision and the appeal

Automated re-classification does not happen on appeal.

---

## Edge Cases

1. **Minimalist poetry** — short lines, simple vocabulary, heavy repetition. Stylometrics will score this AI-like (low variance, low TTR). Mitigation: uncertain band absorbs borderline scores; creator can appeal.
2. **Non-native English writing** — irregular sentence structure and unusual punctuation patterns may read as "unexpectedly human" to the LLM while stylometrics flags it. The two signals could point in opposite directions, producing a mid-range confidence that correctly lands in uncertain.
3. **Very short text (< 80 words)** — stylometric statistics are unreliable. The combined score will be noisier. A future improvement would weight `llm_score` higher (or exclusively) when word count is low.

---

## Architecture

Text enters via `POST /submit`, is rate-checked, scored by both signals, combined into a confidence value, mapped to a label, logged to SQLite, and returned. Appeals enter via `POST /appeal`, update the status of an existing record, and append an appeal entry to the log.

```
POST /submit
    │
    ▼
[Rate Limiter] ──429──► stop
    │
    ├─────────────────────────┐
    ▼                         ▼
[Groq LLM]            [Stylometrics]
 llm_score              stylo_score
    └──────────┬───────────┘
               ▼
      [Confidence Scorer]
       0.6*llm + 0.4*stylo
               │
               ▼
       [Label Generator]
        ≥0.80 → AI
        ≤0.30 → human
        else  → uncertain
               │
               ▼
       [Audit Logger] → SQLite
               │
               ▼
          JSON response


POST /appeal { content_id, reason }
    │
    ▼
[Lookup record] ──404──► stop
    │
    ▼
[status = "under_review"]
    │
    ▼
[Audit Logger] → append appeal
    │
    ▼
JSON response { appeal_id, status }
```

---

## AI Tool Plan

**M3 — Flask skeleton + Groq signal**
- Provide: Detection Signals section + Architecture diagram
- Ask for: Flask app with `POST /submit` stub, Groq signal function that returns `llm_score`
- Verify: call the endpoint with 2–3 texts (one clearly AI, one clearly human), confirm `llm_score` varies plausibly

**M4 — Stylometrics + confidence scoring**
- Provide: Detection Signals + Uncertainty Representation sections + diagram
- Ask for: stylometric analyzer function returning `stylo_score`, confidence combiner
- Verify: run the same texts through both signals; confirm combined scores differ meaningfully between AI and human samples; check uncertain band behaves at boundary values (0.30, 0.31, 0.79, 0.80)

**M5 — Labels + appeals + audit log**
- Provide: Transparency Labels + Appeals Workflow sections + diagram
- Ask for: label generator, `POST /appeal` endpoint, SQLite audit log schema + write/read helpers, `GET /log`
- Verify: trigger all three label variants by submitting texts with known scores; submit an appeal and confirm status flips to `under_review`; check `GET /log` returns at least 3 entries

---

## Implementation Checklist

- [ ] Flask app skeleton + SQLite schema
- [ ] Groq LLM signal (`llm_score`)
- [ ] Stylometric analyzer (`stylo_score`)
- [ ] Confidence scorer + label generator
- [ ] `POST /submit` + rate limiting
- [ ] `POST /appeal`
- [ ] `GET /log` + `GET /status/<id>`
- [ ] README (label variants, rate limit reasoning, audit log sample)
