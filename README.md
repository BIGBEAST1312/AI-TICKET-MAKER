# Ticket Maker

Turns a support interaction into a filed-ready ticket in the four field format a
service desk uses — and refuses to claim anything the notes do not support.

Built after a term on a university IT support desk, where the slow part of a call
is rarely the fix. It is writing the ticket afterwards: the same categories, the
same steps, the same closing email, typed again between clients.

Two ways in:

- **Staff** — paste the interaction and your rough notes, get all four fields plus
  status and priority.
- **Student** — describe the problem, get a clean title and description to paste
  into the request form, plus the questions the desk would have had to ask anyway.

---

## Run it

```bash
pip install -r requirements.txt
python scripts/train.py      # trains both classifiers, about one second
python run.py               
python tests/test_app.py     # 41 tests
python scripts/evaluate.py   # the numbers below
```

None of that needs an API key or network. Claude drafting (below) is optional.

---

## Staff access

Student drafting is open. Staff drafting — the four-field ticket, the resolution
steps, the closing email — is behind a shared desk password, and the check is on
the server:

```bash
TICKET_MAKER_DESK_PASSWORD=... TICKET_MAKER_SECRET_KEY=... python run.py
```

| Route | Does |
|---|---|
| `GET /api/session` | whether staff mode is open, and whether a password is set |
| `POST /api/login` | `{"password": "..."}` → sets an HttpOnly session cookie |
| `POST /api/logout` | clears it |
| `POST /api/draft` | refuses `mode: "staff"` with 401 unless the session holds |

**Why it is not in the browser.** A password compared in JavaScript stops nobody:
it is readable in the page source, and the API can be called directly with curl.
Hiding the form is a convenience for the person using it, not a control. So the
frontend only ever learns yes or no, the password is compared in constant time,
and the cookie is HttpOnly. The test suite proves the point by calling
`/api/draft` directly without a session and asserting a 401.

With no `TICKET_MAKER_DESK_PASSWORD` set, staff mode stays open and
`/api/session` reports `password_required: false` — unprotected, but visibly so
rather than silently.

`TICKET_MAKER_SECRET_KEY` signs the cookie. Left unset, a random key is
generated at startup and every session ends when the server restarts, which is
fine locally and wrong anywhere deployed.

A separate frontend (Vite, Next) calls the API from another origin, so set the
origins allowed to send the cookie:

```bash
TICKET_MAKER_ALLOWED_ORIGINS=http://localhost:5173 python run.py
```

and send `credentials: "include"` on every request from it.

---

### A model writes the wording

Set an API key and a model drafts the title, description, resolution steps and
the closing line's subject, in both Staff and Student mode. Claude or Gemini,
whichever key is set (Claude wins if both are):

```bash
ANTHROPIC_API_KEY=... python run.py      # Claude, default model claude-sonnet-5
GEMINI_API_KEY=... python run.py         # Gemini, default model gemini-flash-latest
python scripts/check_model.py              # one real request, prints the reply or the exact error
TICKET_MAKER_MODEL=...                   # optional, overrides the default model
```

Without a key the template drafter runs instead, and the page says which one
wrote each ticket. `--drafter template` forces the template even with a key set.

The division of labour is the point of the design:

| Decided by | What |
|---|---|
| Trained classifier | category |
| Rules, then classifier | escalation |
| Rules over the notes | resolution status and its closing line |
| Rules | priority |
| Claude or Gemini | wording only — title, description, steps, the issue phrase |

The model is called with a forced tool so its reply always has the expected
shape, and it never sees or sets the status. Every step it writes that is not an
exact article step is marked `[CHECK]`; the honest status line is appended
afterwards by the rules, so a model that writes "resolved" cannot close a ticket;
an escalation's Comment is never rewritten; and if the API fails the template
draft is returned with a flag. Tests stub the API and prove each of these.

The interaction text goes to the model provider's API, which is why the interface warns
against pasting names or student numbers — and why the prompt tells the model to
drop them if they appear anyway.

---

## The idea

An AI that drafts tickets is easy. An AI that drafts tickets a desk can trust is
the actual problem, because the failure mode is not a clumsy sentence — it is a
ticket that says a problem was resolved when nobody watched it get fixed.

So the work is split by how much certainty each part needs:

| Decision | Made by | Why |
|---|---|---|
| Category | Trained classifier | Fuzzy, learnable from examples |
| Above desk level? | Rules first, classifier second | Too expensive to get wrong |
| Resolved or in process? | Rules over the notes | Must never be a guess |
| Priority | Rules | Explainable, auditable |
| Which article applies | TF-IDF similarity | Suggestion, shown with its score |
| Wording | Template, or Claude | Cosmetic, checkable |

Anything a rule decides, the model cannot override.

---

## Guardrails

**It will not claim a resolution it cannot support.** The closing line
`The Client's problem was Successfully resolved.` appears only when the notes say
the fix was seen working with the client present. Notes that say a method was
suggested, or that the client would try it at home, produce **In Process** with
the reason shown. Mixed wording takes the safer status. Empty notes default to
In Process.

**It flags its own guesses.** Steps taken from the matched article appear plain.
Anything else — your own note lines, or a step the model wrote — is prefixed
`[CHECK]` so it is obvious what still needs a human before filing.

**It never closes an escalation.** Account compromise, phishing, token
revocation, device registration, licence purchases and service complaints are
detected and written as escalations with the reason stated.

**It knows what it does not know.** No matching article is stated as such, not
papered over. Missing device, missing error message and missing steps-already-
tried become questions rather than assumptions.

**It carries desk knowledge.** Platform notes fire on the situation, not on
request: ChromeOS and Linux identity format, the macOS certificate prompt, the
password sync delay after a reset, approval prompt versus typed code, the
managed catalogue not existing on personal laptops.

---

## Numbers

From `python scripts/evaluate.py`, 5-fold cross validation on 151 labelled
examples across 7 categories, 25 of them escalations. Cross validation rather
than a score on the training data, because a model graded on what it memorised
tells you nothing.

| Metric | Value |
|---|---|
| Category accuracy | 82.8% |
| Escalation recall — classifier alone | 68.0% |
| Escalation recall — rules alone | 92.0% |
| Escalation recall — combined, as shipped | 96.0% |
| Escalation precision — combined | 82.8% |

**Why the escalation threshold is 0.40.** A missed escalation closes a ticket
that needed someone with more access. A needless one costs a few minutes. So the
classifier fires below the usual half way mark, and the sweep in
`scripts/evaluate.py` shows what that buys:

| Threshold | Recall | Precision |
|---|---|---|
| 0.25 | 100.0% | 49.0% |
| 0.30 | 100.0% | 59.5% |
| 0.35 | 100.0% | 73.5% |
| **0.40** | **96.0%** | **82.8%** |
| 0.50 | 92.0% | 95.8% |
| 0.60 | 92.0% | 100.0% |

0.35 reaches perfect recall, but at 73.5% precision one escalation warning in
four is noise — and a tool that cries escalation constantly gets ignored, which
costs recall in practice rather than on paper. 0.40 was chosen as the point
where the warning is still worth reading.

**The classifier is weaker on escalation than on category, and that is the
point.** The rule layer is what lifts recall from 68% to 96%. The honest framing
is that the model handles the fuzzy job (category) and rules handle the
expensive one.

**Shorthand matters more than model size.** The first version of the data spelled
every term out, so real desk shorthand — "mfa", "nsid", "o365", "cpas" — missed
badly; "mfa not working" was classified as a hardware problem. Adding the way
tickets are actually typed fixed it and lifted overall accuracy by three points.
That is the lesson worth keeping: the data was the bottleneck, not the model.

### Known gaps

One escalation is still missed at the shipped threshold: an access point down for
a whole room, which is an infrastructure report rather than a desk fix. It reads
like an ordinary wireless problem in the text.

Category confusion is concentrated where the categories genuinely overlap. A
wireless failure right after a password reset is arguably both Wireless and
Accounts, and an unrecognised verification method is both MFA and Accounts — in
both cases the ticket is fine either way, and the escalation flag, which is the
part that matters, still fires.

---

## Layout

```
app/
  classify.py     TF-IDF + logistic regression, category and escalation
  rules.py        escalation triggers, resolution honesty, priority, missing info
  kb.py           article matching
  drafting.py     assembles the four fields; template and Claude drafters
  server.py       Flask routes
  data_store.py   loads data/
data/
  interactions.json   151 labelled sample interactions  ← SAMPLE
  kb_articles.json    placeholder articles + platform notes  ← PLACEHOLDER
scripts/train.py, scripts/evaluate.py
templates/, static/
tests/test_app.py
```

Swap those two data files and the behaviour follows; no code changes needed.

---

## About the data

Everything in `data/` was written for development. There is no real client
information anywhere in this repository, and the knowledge base articles are
placeholders — the interface shows a sample-data banner while that is true.

If you adapt this for a real desk:

- use only articles that are publicly published; staff-only article text does not
  belong in a public repository
- de-identify every example — no names, no student or employee numbers, no
  ticket numbers
- check your organisation's policy before putting real interaction details into
  any AI tool

---


