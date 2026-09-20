"""The rule layer: the desk's own judgement, written down.

The classifier is a statistical guess. These rules are the parts that must not
be a guess: whether something goes above desk level, whether a fix was actually
confirmed, and what the ticket is still missing. A rule can override the model,
never the other way round.
"""

import re

# Phrases that put a ticket above desk level regardless of what the model says.
# A false escalation costs a few minutes. A missed one closes a ticket that
# should have gone to someone with more access.
ESCALATION_TRIGGERS = [
    ("compromise", "possible account compromise"),
    ("compromised", "possible account compromise"),
    ("phishing", "client entered credentials on a phishing page"),
    ("phished", "client entered credentials on a phishing page"),
    ("hacked", "possible account compromise"),
    ("suspicious sign", "suspicious sign in activity"),
    ("unrecognised", "unrecognised authentication method on the account"),
    ("unrecognized", "unrecognised authentication method on the account"),
    ("forwarding rule", "unexpected mailbox rule, possible compromise"),
    ("revok", "session or token revocation is above desk level"),
    ("taken over", "possible account compromise"),
    ("entra", "device object changes are above desk level"),
    ("135011", "device registration issue, above desk level"),
    ("device object", "device object changes are above desk level"),
    ("tenant", "tenant side change is above desk level"),
    ("purchase", "licence purchase goes through IT requisitions"),
    ("licence purchase", "licence purchase goes through IT requisitions"),
    ("license purchase", "licence purchase goes through IT requisitions"),
    ("requisition", "licence purchase goes through IT requisitions"),
    ("complaint", "service quality complaint goes to a supervisor first"),
    ("supervisor", "service quality complaint goes to a supervisor first"),
    ("hardware repair", "physical repair is handled by a technician"),
    ("cracked screen", "physical repair is handled by a technician"),
    ("physical repair", "physical repair is handled by a technician"),
    ("poster", "large format printing is not handled at the desk"),
    ("large format", "large format printing is not handled at the desk"),
    ("did not request", "account change the client did not make, possible compromise"),
    ("never asked for", "account change the client did not make, possible compromise"),
    ("did not change", "account change the client did not make, possible compromise"),
]

# Wording that means the fix was watched working, in front of the client.
CONFIRMED_PHRASES = [
    "confirmed",
    "verified working",
    "tested and it worked",
    "signed in successfully",
    "logged in successfully",
    "connected successfully",
    "printed successfully",
    "worked in front of",
    "working before they left",
    "client confirmed",
    "reconnected and it worked",
    "issue resolved at the desk",
]

# Wording that means the client was given a method but was not seen using it.
UNCONFIRMED_PHRASES = [
    "suggested",
    "advised",
    "told them to",
    "asked them to",
    "will try",
    "going to try",
    "left to try",
    "sent them the article",
    "emailed the steps",
    "they will do it later",
    "at home",
    "could not test",
    "did not test",
    "unable to verify",
]

HIGH_PRIORITY_HINTS = [
    "exam",
    "midterm",
    "final",
    "deadline",
    "due today",
    "due tonight",
    "cannot teach",
    "class is starting",
    "whole class",
    "everyone in",
    "multiple clients",
    "compromise",
    "phishing",
    "locked out of everything",
]

LOW_PRIORITY_HINTS = [
    "asking how",
    "wants to know",
    "for future reference",
    "general question",
    "planning to",
    "whenever",
    "no rush",
]

OS_HINTS = [
    "windows",
    "mac",
    "macbook",
    "macos",
    "ios",
    "iphone",
    "ipad",
    "android",
    "chromebook",
    "chromeos",
    "chrome os",
    "linux",
    "ubuntu",
]


def _contains(text, needles):
    lowered = text.lower()
    return [n for n in needles if n in lowered]


def escalation_check(text, model_says_escalate, model_confidence):
    """Rules first, model second. Returns (escalate, reason, source)."""
    hits = [(k, reason) for k, reason in ESCALATION_TRIGGERS if k in text.lower()]
    if hits:
        return True, hits[0][1], "rule"
    if model_says_escalate:
        return (
            True,
            "the classifier judged this above desk level, please confirm before closing",
            "model",
        )
    return False, "", "none"


def resolution_status(notes):
    """Was the fix actually confirmed, or only suggested?

    The default is In Process. A ticket only claims a resolution when the notes
    say the fix was seen working, because a wrongly closed ticket is worse than
    an open one.
    """
    confirmed = _contains(notes, CONFIRMED_PHRASES)
    unconfirmed = _contains(notes, UNCONFIRMED_PHRASES)

    if confirmed and not unconfirmed:
        return {
            "status": "Resolved",
            "closing_line": "The Client's problem was Successfully resolved.",
            "evidence": confirmed,
            "reason": "the notes say the fix was confirmed with the client present",
        }
    if confirmed and unconfirmed:
        return {
            "status": "In Process",
            "closing_line": (
                "The method was provided to the client. The resolution was not "
                "confirmed at the desk."
            ),
            "evidence": confirmed + unconfirmed,
            "reason": (
                "the notes contain both confirmed and unconfirmed wording, so the "
                "safer status was used"
            ),
        }
    return {
        "status": "In Process",
        "closing_line": (
            "The method was provided to the client. The resolution was not "
            "confirmed at the desk."
        ),
        "evidence": unconfirmed,
        "reason": "nothing in the notes confirms the fix was seen working",
    }


def priority(text, escalate):
    lowered = text.lower()
    if escalate or _contains(lowered, HIGH_PRIORITY_HINTS):
        return "High"
    if _contains(lowered, LOW_PRIORITY_HINTS) and "cannot" not in lowered:
        return "Low"
    return "Medium"


def missing_information(text, category):
    """What a ticket in this category needs that the text does not contain."""
    lowered = text.lower()
    questions = []

    if category in {"Wireless", "Device", "Software", "Microsoft365", "Printing"}:
        if not _contains(lowered, OS_HINTS):
            questions.append("What device and operating system is this on?")

    if not re.search(r"(error|code|message|says|reads)", lowered):
        questions.append("Is there an exact error message on screen?")

    if not re.search(r"(tried|already|restarted|attempted)", lowered):
        questions.append("What has already been tried?")

    if category == "Wireless" and "password" not in lowered:
        questions.append("Has the account password been changed recently?")
    if category == "Printing" and not re.search(r"(credit|balance|fund)", lowered):
        questions.append("What is the current print balance on the account?")
    if category == "MFA" and not _contains(lowered, ["app", "text", "sms", "call"]):
        questions.append("Which verification method is registered on the account?")

    return questions


def platform_warnings(text, category, notes):
    """Desk knowledge that applies to this ticket, from data/kb_articles.json."""
    lowered = text.lower()
    out = []
    for note in notes:
        if category not in note["applies_to"]:
            continue
        if any(trigger in lowered for trigger in note["trigger"]):
            out.append({"id": note["id"], "note": note["note"]})
    return out
