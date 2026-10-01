# Jev (TypeSafe) API — how to call it and common patterns

> Source: https://docs.typesafe.ai (read 2026-09-30). Jev is now called **directly** at
> `api.typesafe.ai` — the old Vercel AI Gateway route (`ai-gateway.vercel.sh/typesafe/...`)
> is no longer used.

## What Jev is

Jev is TypeSafe's flagship model and the first **System One** model. It is *not* a
text-generation LLM: it does not chat, write code, or stream text. You send it a
**state** (content to evaluate) and a set of **typed questions**, and it returns
**structured answers** — typed values and probability distributions your code can
branch on directly. No prompt-to-JSON parsing.

It is worth reaching for when code needs to:
- route a request to one of a fixed set of destinations, with a confidence on the routing;
- score something on a rubric and branch on the number;
- check whether a statement is true of a document before acting;
- replace a fragile "return JSON" LLM prompt with typed values by construction.

It is *not* a drop-in for the LLM behind a coding agent, and it is not good at free-form
generation (see jaggedness, below).

## The API call

```http
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

Keys are created at https://console.typesafe.ai/keys. A playground is at
https://console.typesafe.ai/playground.

### Models

| Name | Points to | Notes |
| - | - | - |
| `jev-latest` | `jev-1.13.0` | Default. Use in new code. |
| `jev-preview` | `jev-1.13.0` | Moves ahead when a preview build ships. |
| `jev-1.13.0` | — | Versioned ID. **Pin this if you tune confidence thresholds** against a specific version — aliases move under you. |

All models serve the same endpoint; the request's `model` field selects. `GET /v1/models`
lists what your account can use.

### Request body

```json
{
  "state": "Help! My payouts have been failing for 3 days.",
  "model": "jev-latest",
  "questions": {
    "is_urgent": {
      "type": "noul",
      "instructions": "Does this convey urgency?"
    }
  }
}
```

- `state` (required): string, JSON object, or array of text values. Text only (no
  images/audio/video). Prefer a **named object** for multi-part context; a plain string
  for simple cases.
- `model` (required): see above.
- `questions` (required): a map of `question id → Question`. You choose the ids; answers
  come back under the same ids. The id is not sent to the model.

`instructions` can be a string, an object, or an array — e.g. put data in one field and
the question in another, referring to fields by name in backticks:

```json
"instructions": {
  "potential_duplicate": { "name": "John Smith", "location": "Oakland, California" },
  "question": "Is the resume for the same person as `potential_duplicate`?"
}
```

### Question types

| Type | Question | Answer |
| - | - | - |
| **Noul** | `{"type": "noul", "instructions": "...", "criteria": {"true": "...", "false": "..."}}` (criteria optional) | `{"type": "noul", "noul": 0.95}` — P(yes), 0–1. **No confidence field.** |
| **Choice** | `{"type": "choice", "instructions": "...", "criteria": {option: rubric-or-null}}` — max 255 options | `{"type": "choice", "choice": "...", "probabilities": {option: p}, "confidence": 0.81}` |
| **Score** | `{"type": "score", "instructions": "...", "criteria": ["level 0", "level 1", ...]}` — 2 to 10 ordered levels | `{"type": "score", "score": 1.05, "legend": {"0": "Calm", ...}, "probabilities": {"0": 0.0, "1": 0.95}, "confidence": 0.92}` — probability-weighted value, can land between levels |

All three types can be mixed in one request; every question is evaluated against the same
state, in parallel.

Choice example:

```json
"department": {
  "type": "choice",
  "instructions": "Which team should handle this?",
  "criteria": {
    "billing": "Payments, invoicing, refunds",
    "technical": "Bugs, outages, integrations",
    "sales": "Pricing, upgrades, new accounts"
  }
}
```

### Response body

```json
{
  "model": "jev-1.13.0",
  "answers": {
    "department": {
      "type": "choice",
      "choice": "billing",
      "probabilities": { "billing": 0.88, "technical": 0.12, "sales": 0.0 },
      "confidence": 0.81
    }
  },
  "usage": { "input_tokens": 318, "output_tokens": 34 }
}
```

- `model`: the versioned ID that actually answered (log it).
- `answers`: one entry per question, keyed by your ids.
- `usage`: input/output token counts.

### Errors

| Status | Meaning | Handling |
| - | - | - |
| `401` | Missing/invalid API key | Fix the `Authorization` header. |
| `422` | Request failed validation (missing field, malformed question) | Body names the offending field. |
| `429` | Rate limit exceeded | Back off; honor `retry-after` when present. |
| `529` | TypeSafe temporarily overloaded | Retry with exponential backoff. |

### Pricing & limits (jev-1.13)

- **$42 per Btok input** ($0.042/Mtok); output tokens are free.
- Rate limits: 100K tokens/s and 40 requests/s (adjusting dynamically — can change without
  notice; higher limits on enterprise plans).
- Context: 64k tokens per request total; 32k for `state` + the single longest question.
- English is the primary training language; other languages (incl. CJK) are handled but
  less accurately.

## Python SDK

```sh
pip install typesafe-sdk        # or: uv add typesafe-sdk
                                # optional: typesafe-sdk[http2]
```

Env vars: `TYPESAFE_API_KEY` (required), `TYPESAFE_BASE_URL`, `TYPESAFE_DEFAULT_MODEL`,
`TYPESAFE_LOG_LEVEL`. SDK defaults: base URL `https://api.typesafe.ai`, model
`jev-latest`, 10s timeout per HTTP operation, built-in retries with backoff that honor
`retry-after`.

```python
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

with TypeSafeClient() as client:
    response = client.system_one(
        state={"document": "I was charged twice. Please fix this ASAP."},
        questions={
            "billing": Noul(instructions="Is this ticket about billing?"),
            "tone": Choice(instructions="What is the customer's tone?",
                           criteria={"calm": None, "frustrated": None, "angry": None}),
            "urgency": Score(instructions="How urgent is this ticket?",
                             criteria=["can wait", "this week", "today"]),
        },
    )

print(response.nouls["billing"].noul)      # 0–1
print(response.choices["tone"].choice)     # str
print(response.scores["urgency"].score)    # float
```

`AsyncTypeSafeClient` is the same API with `await`. Typed exceptions:
`TypeSafeAuthenticationError` (401), `TypeSafeUnprocessableEntityError` (422),
`TypeSafeRateLimitError` (429, with `.retry_after_ms`), `TypeSafeInternalServerError`
(5xx), `TypeSafeAPIConnectionError` / `TypeSafeAPITimeoutError`, and
`TypeSafeAPIResponseValidationError` (200 but structurally invalid body).

### How OpenBook uses it

- `py_services/jev_client.py` — thin `httpx` wrapper: `JevClient(api_key).ask(state, questions)`.
  Now points at `https://api.typesafe.ai/v1/systemone` with `model: "jev-latest"`.
  (The request/response shape is identical to what the Vercel gateway proxied, so the
  verify service's `parse_answer` — `response["answers"]["speaker"]["choice" / "confidence"]`
  — works unchanged.)
- `py_services/jev_service.py` — loads the key from env.
- `py_services/openbook_parser/jev_verify_service.py` — the dialogue verification layer
  (gates, roster, Q1 line query / Q2 run query, cache, verdict table, additive apply).

## Confidence

Every **Choice** and **Score** answer carries `confidence` ∈ [0, 1], derived from the
*shape* of its probability distribution (concentrated ⇒ high, flat ⇒ low). For a Choice
over n options it is approximately `(n × peak − 1) / (n − 1)`. **Noul answers do not carry
confidence** — the noul value itself is the probability.

- You are not locked into their definition: the full `probabilities` map is always
  returned, so you can compute your own measure.
- "I don't know" is a first-class signal: low confidence is the model telling you it
  lacks information or the question is a poor fit.
- The docs' suggested three bands: **high** → act automatically; **medium** → proceed with
  caution (confirm / flag for review); **low** → do not act (route to human / fallback).
- **Thresholds scale with risk**, not with the model: within one system, gate each
  action at the level its consequences deserve (e.g. read-only at 0.6, destructive at
  0.85+). Start conservative and calibrate on your own data.

## Common patterns

### 1. Speculative fan-out

Put **all** the questions your system needs in **one** request — including questions that
only matter conditionally — and let code decide what's relevant after the fact. All
questions are evaluated in parallel, so extra questions cost little latency (and the
cookbooks show large cost wins: e.g. a 13-question call 12.2× cheaper and 10× faster than
13 calls, with unchanged answers).

```python
# ask category + bug_severity + has_repro_steps + refund_requested + frustration
# in one call; ignore the answers that don't apply to the category:
category = response.answers["category"]
if category.choice == "bug_report":
    severity = response.answers["bug_severity"].score
    repro = response.answers["has_reproducible_steps"].noul
    ...
elif category.choice == "billing":
    refund = response.answers["refund_requested"].noul
    ...
```

This is the shape of our Q2 "run query": one call over a whole untagged run, one
question per line in the run.

### 2. Confidence-gated routing

The answer tells you **what**; confidence tells you **whether to act**. Use confidence as
a second decision axis, with per-action thresholds:

```python
action = response.answers["intent"]
if action.confidence < 0.6:
    route_to_human()                    # model is genuinely unsure
elif action.choice == "check_balance":
    show_balance()                      # low stakes → lower bar
elif action.choice == "approve_transfer":
    if action.confidence > 0.85:
        approve_transfer()              # high stakes, high confidence
    else:
        ask_user_to_confirm()           # high stakes, moderate confidence
```

This is exactly our FP-minimization policy: a wrong *assert* is the high-stakes action,
so it gets the highest bar (and below-bar outcomes degrade to suggestion/unknown instead
of acting).

### 3. Composite scoring

Break a complex judgment into independent **Score** questions (one per dimension), then
normalize (divide by level count) and combine with **weights you control in code**:

```python
py, lead, arch, general = (response.answers[k].score / 4 for k in (...))
ic_score = 0.40*py + 0.10*lead + 0.40*arch + 0.10*general
em_score = 0.15*py + 0.40*lead + 0.20*arch + 0.25*general
```

You get one adjustable composite per rubric, with full visibility into how the number was
built — and the model only ever makes atomic judgments.

### 4. Intent routing

Use Jev as a fast front classifier that routes each request to the cheapest correct
handler: deterministic code, a specialist LLM, or a human. Classify intent (Choice) +
complexity (Score) in one call; route low-confidence intents straight to a human; use the
complexity score to split "LLM can handle" from "needs escalation". The expensive
resources only run for the requests that need them.

## State guidance (from the docs)

- Separate **content** (state) from **judgments** (questions).
- Prefer named JSON objects so relationships are explicit; string only for simple cases.
- **Send only what the question needs.** Accuracy falls as state grows with irrelevant
  detail (distractors + "context rot"); filter/retrieve in code first. When you can't
  filter the state, a Noul relevance question can gate downstream use.
- 32k-token budget for `state` + longest question — size your batch queries to it.

## Known jaggedness (jev-1.13) — design around these

| Failure mode | Do this instead |
| - | - |
| Literal reading — answers the words, not the intent | Write the exact condition; put boundary cases in `criteria`; if interpretation is unavoidable, split into two literal questions and combine in code |
| Math / counting / numeric precision | Keep all arithmetic and counting in code |
| Date/time comparison | Model extracts components (Choice over closed sets, incl. "not stated"); code does ordering/duration |
| Indirection (multi-hop, double negatives) | Write instructions as directly as possible; name the relevant state parts |
| Large state full of irrelevant detail | Filter in code; send only what the question needs |
| Adversarial content in state | Be explicit in criteria; test edge cases before deploying |
| Contradictory instructions vs criteria | Align them; criteria are an extension of the instruction |
| Structural invariants not guaranteed | `noul` vs `probabilities["yes"]` for the same question can diverge (0.22 vs 0.01 observed); a question and its negation need not sum to 1; **don't carry a threshold tuned on one question type over to another** |
| Generation | Jev is not a generator. For extraction, generate candidates with regex/LLM, then let Jev *pick* via Choice |

## Implications for the OpenBook verification work

- Keep `state` minimal per query (the line's local context + roster), not whole chapters,
  unless the run-query pattern needs it — and batch the questions, not the context.
- Our "agree / disagree / None + confidence" verdict table is the confidence-gated
  routing pattern with a deliberately high bar for the destructive action (assert) and a
  safe fallback (unknown + speaker guess) below it.
- Thresholds (0.40 note / 0.70 promote / 0.60 downgrade) are tuned to *our* outcome
  space, not to the docs' examples; re-validate if the pinned model version changes.
- Never ask Jev to count, compare numbers/dates, or generate text — do those in code.
- Log the response `model` field (versioned ID) with each cached verdict.
