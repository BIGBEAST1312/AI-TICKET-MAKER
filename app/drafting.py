"""Turning an interaction into the four field ticket the desk actually files.

Two drafters share one contract:

  template  works with no API key and no network. Steps come straight out of the
            matched article, so nothing can be invented.
  claude /  write in the staff member's words using the Claude or Gemini API,
  gemini    but are only
            ever shown the matched article, and every line it produces that is
            not traceable to that article is flagged for review.

Whichever runs, the ticket is assembled the same way and the same rules decide
status, priority and escalation.
"""

import json
import os
import re

from app import rules
from app.classify import predict
from app.kb import ArticleIndex

FLAG = "[CHECK] "

# A line in an article's steps marking where chosen optional steps are placed.
OPTIONS_MARKER = "{options}"

CLOSING_TEMPLATE = """Hi {client},

Thank you for contacting IT Support Services for help with {issue}.

If you have any further questions on this ticket please include them in a reply to this email.

Please take time to provide feedback about the service you received within this ticket request.

For any new requests or incidents please email itsupport@usask.ca.

Have a great day.

Regards,

IT Support Services"""

DEFAULTS = {
    "client": "[client name]",
    "desk": "Murray IT Support Desk",
    "agent": "Aarya",
    "agent_title": "IT Support Student Assistant",
}

CATEGORY_LABELS = {
    "Accounts": "Account",
    "Wireless": "Wireless",
    "MFA": "Multi Factor Authentication",
    "Microsoft365": "Microsoft 365",
    "Printing": "Printing",
    "Software": "Software",
    "Device": "Device",
}


def _normalise(line):
    return re.sub(r"[^a-z0-9 ]", "", line.lower()).strip()


def _title(text, category):
    symptom = re.sub(r"^(client|student|the client|user)\s+", "", text.strip(), flags=re.I)
    symptom = symptom.rstrip(". ")
    words = symptom.split()
    if len(words) > 10:
        symptom = " ".join(words[:10])
    return f"{CATEGORY_LABELS.get(category, category)}: {symptom[0].lower() + symptom[1:]}"


def _description(text):
    text = text.strip()
    if not text.endswith("."):
        text += "."
    return text[0].upper() + text[1:]


def _issue_phrase(category):
    return {
        "Accounts": "your account",
        "Wireless": "connecting to the campus wireless network",
        "MFA": "multi factor authentication",
        "Microsoft365": "Microsoft 365",
        "Printing": "printing",
        "Software": "software access",
        "Device": "your device",
    }.get(category, "your request")


# Device detection for articles with per-device steps. Kept here so this file
# works on its own. ChromeOS is checked before anything that might match
# "chrome", and whole words only, so "mac" does not fire on "machine".
PLATFORM_PATTERNS = [
    ("remarkable", r"\bremarkable\b"),
    ("chromeos", r"\b(chromebook|chromeos|chrome os)\b"),
    ("ios", r"\b(iphone|ipad|ios)\b"),
    ("android", r"\b(android|pixel|galaxy|samsung phone)\b"),
    ("macos", r"\b(mac|macbook|macos|os x|imac)\b"),
    ("linux", r"\b(linux|ubuntu|fedora|debian|mint)\b"),
    ("windows", r"\b(windows|win11|win10|surface)\b"),
]


def _detect_platform(text):
    """The device the interaction is about, or None when it does not say."""
    lowered = text.lower()
    for platform, pattern in PLATFORM_PATTERNS:
        if re.search(pattern, lowered):
            return platform
    return None


def _article_steps(article, platform):
    """The article's steps for this device when it has them, else its general steps."""
    if not article:
        return []
    by_platform = article.get("platform_steps") or {}
    return by_platform.get(platform) or article.get("steps", [])


def _optional_step(article, line):
    """The optional step this note refers to, when every word of the note is in it."""
    words = set(_normalise(line).split())
    if not article or len(words) < 3:
        return None
    for option in article.get("optional_steps", []):
        if words <= set(_normalise(option).split()):
            return option
    return None


class Drafter:
    def __init__(self, drafter="auto", settings=None):
        if drafter == "auto":
            if os.environ.get("ANTHROPIC_API_KEY"):
                drafter = "claude"
            elif os.environ.get("GEMINI_API_KEY"):
                drafter = "gemini"
            else:
                drafter = "template"
        self.drafter = drafter
        self.index = ArticleIndex()
        self.settings = {**DEFAULTS, **(settings or {})}

    # ------------------------------------------------------------------ public

    def draft(self, text, notes="", mode="staff"):
        """mode 'staff' returns all four fields, 'student' only what a student can give."""
        prediction = predict(text)
        article, score = self.index.best_match(text, prediction.category)
        platform = _detect_platform(f"{text} {notes}")
        self.platform = platform

        escalate, reason, source = rules.escalation_check(
            f"{text} {notes}", prediction.escalate, prediction.escalate_confidence
        )
        status_info = rules.resolution_status(notes)
        if escalate:
            status_info = {
                "status": "In Process - escalated",
                "closing_line": f"Escalated: {reason}.",
                "evidence": [],
                "reason": "escalations are never closed at the desk",
            }

        result = {
            "mode": mode,
            "category": prediction.category,
            "category_confidence": round(prediction.category_confidence, 3),
            "driving_terms": prediction.top_terms,
            "escalate": escalate,
            "escalation_reason": reason,
            "escalation_source": source,
            "priority": rules.priority(text, escalate),
            "status": status_info["status"],
            "status_reason": status_info["reason"],
            "article": (
                {
                    "id": article["id"],
                    "title": article["title"],
                    "synthetic": article.get("synthetic", False),
                    "score": round(score, 3),
                    "url": article.get("url", ""),
                    "has_steps": bool(_article_steps(article, platform)),
                    "platform": platform if platform in (article.get("platform_steps") or {}) else None,
                }
                if article
                else None
            ),
            "missing_information": rules.missing_information(text, prediction.category),
            "platform_warnings": rules.platform_warnings(
                f"{text} {notes}", prediction.category, self.index.platform_notes
            ),
            "flags": [],
            "fields": {},
        }

        result["fields"]["Title of Request"] = _title(text, prediction.category)
        result["fields"]["Description of Request"] = _description(text)
        result["drafter"] = "template"

        if mode == "student":
            result["fields"]["What I have already tried"] = notes.strip() or "[not provided]"
            result["note_to_student"] = (
                "Paste these fields into the IT request form. The support desk writes "
                "the resolution and the reply."
            )
            if self.drafter in PROVIDERS:
                self._rewrite_with_claude(result, text, notes, article, mode, prediction.category)
            return result

        comment, flags = self._comment(text, notes, article, escalate, status_info)
        result["fields"]["Comment"] = comment
        result["flags"].extend(flags)
        result["fields"]["Closing statement"] = self._closing(
            prediction.category, escalate, status_info
        )

        if self.drafter in PROVIDERS:
            self._rewrite_with_claude(
                result, text, notes, article, mode, prediction.category, escalate, status_info
            )

        if article and article.get("synthetic"):
            result["flags"].append(
                "The knowledge base is still placeholder content, so the steps are not real procedure."
            )
        return result

    # ----------------------------------------------------------------- private

    def _comment(self, text, notes, article, escalate, status_info):
        """Resolution steps only. Anything not from the article is flagged."""
        lines = ["The steps taken to resolve the problem:", "Verified client ID"]
        flags = []

        if escalate:
            lines.append(FLAG + "Escalated rather than resolved at the desk")
            flags.append("Escalation: confirm the reason and the queue it was sent to.")
        elif article and _article_steps(article, self.platform):
            pass  # filled below, once the notes have been read
        elif article:
            # A linked article with no steps recorded yet: never pad the ticket with
            # filler. Your own notes become the steps, each marked for review.
            flags.append(
                f"{article['title']} has no steps recorded yet, so the Comment uses your notes only."
            )
        else:
            lines.append(FLAG + "No knowledge base article matched, steps written by hand")
            flags.append("No article matched this problem. Write the steps yourself.")

        steps = [] if escalate else _article_steps(article, self.platform)
        fixed = [step for step in steps if step != OPTIONS_MARKER]
        chosen, extra = [], []
        for line in [ln.strip() for ln in notes.splitlines() if ln.strip()]:
            # A note that restates an article step, even with extra words on the end
            # ("..., confirmed working"), is that step, not a new one to review.
            if any(_normalise(line).startswith(_normalise(step)) for step in fixed):
                continue
            # A short note naming one of the article's optional steps
            # ("not a member of the permission groups") becomes that step in full.
            option = _optional_step(article, line)
            if option:
                chosen.append(option)
            else:
                extra.append(FLAG + line)

        # Chosen options go where the article's {options} line sits, or after the
        # steps when it has none; anything unrecognised follows, marked for review.
        if OPTIONS_MARKER in steps:
            at = steps.index(OPTIONS_MARKER)
            lines.extend(fixed[:at] + chosen + fixed[at:])
        else:
            lines.extend(fixed + chosen)
        lines.extend(extra)
        if notes.strip():
            flags.append(
                "Lines marked with [CHECK] came from your notes, not the article. "
                "Confirm or remove them before filing."
            )

        lines.append(status_info["closing_line"])
        return "\n".join(lines), flags

    def _closing(self, category, escalate, status_info):
        if escalate:
            outcome = (
                "This has been passed to the team that handles this type of request. "
                "They will be in touch with you directly."
            )
        elif status_info["status"] == "Resolved":
            outcome = "The issue was resolved while you were at the desk."
        else:
            outcome = (
                "The steps were provided to you to complete. Please let us know if the "
                "issue continues after trying them."
            )
        return CLOSING_TEMPLATE.format(
            issue=_issue_phrase(category), outcome=outcome, **self.settings
        )

    # ------------------------------------------------------------ Claude drafting

    def _call_claude(self, system, user):
        """One structured call. Returns the tool input as a dict.

        A forced tool call is used instead of asking for JSON in prose, so the
        reply always has the shape the code expects.
        """
        import anthropic

        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        message = client.messages.create(
            model=os.environ.get("TICKET_MAKER_MODEL", PROVIDERS["claude"]["model"]),
            max_tokens=1000,
            system=system,
            tools=[DRAFT_TOOL],
            tool_choice={"type": "tool", "name": DRAFT_TOOL["name"]},
            messages=[{"role": "user", "content": user}],
        )
        for block in message.content:
            if getattr(block, "type", "") == "tool_use":
                return dict(block.input)
        raise ValueError("no structured reply")

    def _call_gemini(self, system, user):
        """Structured call to Gemini, resilient to the free tier's busy spells.

        Busy (503), rate limited (429) and server errors (500) are retried with a
        short backoff; if the first model stays busy, the next one in the list is
        tried. TICKET_MAKER_MODEL may name one model or a comma separated list.
        """
        import time
        import urllib.error

        models = [
            m.strip()
            for m in os.environ.get("TICKET_MAKER_MODEL", PROVIDERS["gemini"]["model"]).split(",")
            if m.strip()
        ]
        last_error = None
        for model in models:
            for attempt in range(RETRIES):
                try:
                    payload = self._gemini_request(model, system, user)
                    self.last_model = model
                    return payload
                except urllib.error.HTTPError as exc:
                    if exc.code not in RETRYABLE:
                        raise  # a bad key or bad request will not fix itself
                    last_error = exc
                    if attempt < RETRIES - 1:
                        time.sleep(BACKOFF_SECONDS * (2**attempt))
        raise last_error

    def _gemini_request(self, model, system, user):
        """One request to one model. Plain HTTP, so no extra dependency."""
        import urllib.request

        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "title": {"type": "STRING"},
                        "description": {"type": "STRING"},
                        "steps": {"type": "ARRAY", "items": {"type": "STRING"}},
                        "issue": {"type": "STRING"},
                    },
                    "required": ["title", "description", "steps", "issue"],
                },
                # Newer Flash models think before answering and that counts
                # against this budget, so leave room for both.
                "maxOutputTokens": 8192,
            },
        }
        request = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                # In a header, not the URL, so the key never lands in a log line.
                "x-goog-api-key": os.environ["GEMINI_API_KEY"],
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            reply = json.loads(response.read().decode("utf-8"))
        candidate = (reply.get("candidates") or [{}])[0]
        parts = candidate.get("content", {}).get("parts", [])
        # Thinking models can return thought parts first; the answer is the text part.
        text = next((part["text"] for part in parts if "text" in part and not part.get("thought")), None)
        if not text:
            reason = candidate.get("finishReason") or reply.get("promptFeedback", {}).get("blockReason")
            raise ValueError(f"no text in the reply (finishReason {reason or 'unknown'})")
        return json.loads(text)

    def _rewrite_with_claude(
        self, result, text, notes, article, mode, category, escalate=False, status_info=None
    ):
        """Claude writes the wording. The rules have already decided the facts —
        category, escalation, status, priority — and nothing here can change them.
        """
        provider = PROVIDERS[self.drafter]
        if not os.environ.get(provider["key"]):
            result["flags"].append(f"No {provider['key']} set, so the template wording was kept.")
            return
        if self.drafter == "claude":
            try:
                import anthropic  # noqa: F401
            except ImportError:
                result["flags"].append("The anthropic package is not installed, template wording kept.")
                return

        allowed = [
            step
            for step in _article_steps(article, getattr(self, "platform", None))
            + ((article or {}).get("optional_steps") or [])
            if step != OPTIONS_MARKER
        ]
        user = "\n".join(
            [
                f"Mode: {mode}",
                f"Category (already decided): {category}",
                f"Escalated (already decided): {'yes' if escalate else 'no'}",
                f"Client interaction: {text}",
                f"{'Staff notes' if mode == 'staff' else 'What the student already tried'}: {notes or '(none)'}",
                f"Knowledge base article: {article['title'] if article else '(none matched)'}",
                f"Article steps: {json.dumps(allowed)}",
            ]
        )
        try:
            call = self._call_gemini if self.drafter == "gemini" else self._call_claude
            payload = call(SYSTEM_PROMPT, user)
        except Exception as exc:  # the template draft is already valid, so degrade quietly
            result["flags"].append(
                f"{provider['name']} drafting failed ({_describe_error(exc)}), template kept."
            )
            return

        result["drafter"] = self.drafter
        result["model"] = getattr(self, "last_model", None)
        title = str(payload.get("title", "")).strip()
        description = str(payload.get("description", "")).strip()
        if title:
            result["fields"]["Title of Request"] = title[:120]
        if description:
            result["fields"]["Description of Request"] = description

        if mode == "student" or escalate:
            # Students do not write the resolution, and an escalation's Comment is
            # the rules' wording, not the model's.
            return

        steps = [str(step).strip() for step in payload.get("steps", []) if str(step).strip()]
        if steps:
            checked = ["The steps taken to resolve the problem:", "Verified client ID"]
            invented = 0
            for step in steps:
                if any(_normalise(step) == _normalise(a) for a in allowed):
                    checked.append(step)
                else:
                    checked.append(FLAG + step)
                    invented += 1
            # The last line is the honest status line the rules chose. Always kept.
            checked.append(status_info["closing_line"])
            result["fields"]["Comment"] = "\n".join(checked)
            if invented:
                result["flags"].append(
                    f"{invented} step(s) in the Comment were written from your notes, not copied "
                    "from the article, and are marked [CHECK]."
                )

        issue = str(payload.get("issue", "")).strip().rstrip(".")
        if issue and len(issue) <= 80:
            result["fields"]["Closing statement"] = result["fields"]["Closing statement"].replace(
                f"for help with {_issue_phrase(category)}.", f"for help with {issue}.", 1
            )


SYSTEM_PROMPT = """You draft tickets for a university IT support desk.

You write wording only. Category, escalation, status and priority have already been decided by the desk's rules. Never contradict them, never claim a problem was resolved, and never add a step that did not happen.

Title: system plus symptom, under 12 words, searchable. Example: "Wireless: uofs-secure fails on Chromebook after password reset".
Description: the client's issue as presented, in one or two plain sentences, third person ("The client..."). No names, student numbers or other identifying details, even if they appear in the input.
Steps (staff mode only): what was actually done at the desk, one action per step, in order, taken from the staff notes. Where a step matches an article step, use the article's exact wording. Do not include "Verified client ID" or any resolution line; those are added for you. If the notes describe nothing done, return no steps.
Issue: a short lowercase phrase for "Thank you for stopping by for help with ___", such as "connecting your Chromebook to uofs-secure"."""

DRAFT_TOOL = {
    "name": "draft_ticket",
    "description": "Return the wording for the ticket fields.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "string"}},
            "issue": {"type": "string"},
        },
        "required": ["title", "description", "steps", "issue"],
    },
}


PROVIDERS = {
    "claude": {"name": "Claude", "key": "ANTHROPIC_API_KEY", "model": "claude-sonnet-5"},
    "gemini": {"name": "Gemini", "key": "GEMINI_API_KEY", "model": "gemini-flash-latest,gemini-flash-lite-latest"},
}


def _describe_error(exc):
    """The provider's own reason, not just the exception's class name.

    "HTTPError" alone cannot tell a bad key from a retired model from a spent
    quota; the status code and the provider's message can.
    """
    import urllib.error

    if isinstance(exc, urllib.error.HTTPError):
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = body.get("error", {}).get("message", "")
        except Exception:
            pass
        hint = {
            400: "check the API key",
            403: "the key is not allowed to use this API",
            404: "the model name was not found; set TICKET_MAKER_MODEL",
            429: "free tier quota reached; wait a minute or try tomorrow",
        }.get(exc.code, "")
        parts = [f"HTTP {exc.code}"] + [p for p in (hint, detail[:160]) if p]
        return " — ".join(parts)
    status = getattr(exc, "status_code", None)
    message = str(exc)[:160]
    return f"{exc.__class__.__name__}{f' {status}' if status else ''}: {message}" if message else exc.__class__.__name__

RETRYABLE = {429, 500, 503}
RETRIES = 3
BACKOFF_SECONDS = 1.0
