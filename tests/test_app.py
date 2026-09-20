"""Tests for the behaviour that must not drift.

Most of these are about honesty rather than accuracy: the ticket must not claim
a resolution that did not happen, must not invent steps, and must not close an
escalation.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import rules  # noqa: E402
from app.drafting import Drafter  # noqa: E402
from app.server import create_app  # noqa: E402

DRAFTER = Drafter()


class ResolutionHonesty(unittest.TestCase):
    def test_confirmed_fix_is_resolved(self):
        result = rules.resolution_status("Reset the password, client signed in successfully at the desk.")
        self.assertEqual(result["status"], "Resolved")

    def test_suggested_fix_is_in_process(self):
        result = rules.resolution_status("Advised the client to reinstall at home and try again.")
        self.assertEqual(result["status"], "In Process")

    def test_empty_notes_default_to_in_process(self):
        self.assertEqual(rules.resolution_status("")["status"], "In Process")

    def test_mixed_notes_take_the_safer_status(self):
        result = rules.resolution_status(
            "Wireless confirmed working, advised them to update the VPN at home later."
        )
        self.assertEqual(result["status"], "In Process")

    def test_resolution_line_only_appears_when_confirmed(self):
        unconfirmed = DRAFTER.draft(
            "Client cannot connect to uofs-secure on a Chromebook",
            notes="Told them to try the full username at home.",
        )
        self.assertNotIn("Successfully resolved", unconfirmed["fields"]["Comment"])


class Escalation(unittest.TestCase):
    def test_compromise_is_escalated_by_rule(self):
        result = DRAFTER.draft("Client clicked a phishing link and entered their password")
        self.assertTrue(result["escalate"])
        self.assertEqual(result["escalation_source"], "rule")

    def test_escalated_ticket_is_never_closed(self):
        result = DRAFTER.draft("Device object disabled in Entra ID, desktop apps failing")
        self.assertIn("escalated", result["status"].lower())
        self.assertNotIn("Successfully resolved", result["fields"]["Comment"])

    def test_ordinary_ticket_is_not_escalated(self):
        result = DRAFTER.draft("Student forgot their password and cannot sign in to the portal")
        self.assertFalse(result["escalate"])


class Drafting(unittest.TestCase):
    def test_all_four_fields_for_staff(self):
        result = DRAFTER.draft("Print job denied at the library printer", notes="Added credit, confirmed printing.")
        for field in ["Title of Request", "Description of Request", "Comment", "Closing statement"]:
            self.assertIn(field, result["fields"])

    def test_student_mode_has_no_comment_or_closing(self):
        result = DRAFTER.draft("My laptop will not join the wifi", mode="student")
        self.assertNotIn("Comment", result["fields"])
        self.assertNotIn("Closing statement", result["fields"])

    def test_note_lines_outside_the_article_are_flagged(self):
        result = DRAFTER.draft(
            "Client cannot connect to the wireless network on Windows",
            notes="Rebuilt the network adapter driver from scratch",
        )
        self.assertIn("[CHECK]", result["fields"]["Comment"])

    def test_comment_has_no_client_facing_language(self):
        result = DRAFTER.draft("Client needs Office installed", notes="Installed and activated, confirmed working.")
        comment = result["fields"]["Comment"].lower()
        self.assertNotIn("thank you", comment)
        self.assertNotIn("hi ", comment)

    def test_closing_statement_is_signed(self):
        result = DRAFTER.draft("Client needs Office installed on a personal laptop")
        self.assertIn("IT Support Services", result["fields"]["Closing statement"])

    def test_article_without_steps_never_pads_the_comment(self):
        result = DRAFTER.draft(
            "Client needs SPSS for a course", notes="Opened the remote lab and launched it, confirmed working"
        )
        comment = result["fields"]["Comment"]
        self.assertNotIn("provided URL", comment)
        self.assertIn("[CHECK] Opened the remote lab", comment)

    def test_matched_article_carries_its_link(self):
        result = DRAFTER.draft("Client forgot their NSID password")
        self.assertTrue(result["article"]["url"].startswith("https://"))

    def test_placeholder_knowledge_base_is_declared(self):
        result = DRAFTER.draft("Client forgot their password")
        self.assertTrue(any("placeholder" in flag for flag in result["flags"]))


class RulesAndPrompts(unittest.TestCase):
    def test_missing_device_is_asked_about(self):
        questions = rules.missing_information("Wireless will not connect", "Wireless")
        self.assertTrue(any("operating system" in q for q in questions))

    def test_platform_warning_for_chromeos(self):
        result = DRAFTER.draft("Chromebook will not join the secure wireless network")
        self.assertTrue(any(w["id"] == "identity-format" for w in result["platform_warnings"]))

    def test_priority_rises_for_a_deadline(self):
        self.assertEqual(rules.priority("cannot submit, assignment due tonight", False), "High")


class Api(unittest.TestCase):
    def setUp(self):
        self.client = create_app().test_client()

    def test_draft_endpoint_returns_fields(self):
        response = self.client.post("/api/draft", json={"text": "Client cannot print from the lab computer"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Title of Request", response.get_json()["fields"])

    def test_default_mode_is_staff(self):
        response = self.client.post("/api/draft", json={"text": "Client cannot connect to the wireless network"})
        fields = response.get_json()["fields"]
        self.assertIn("Comment", fields)
        self.assertIn("Closing statement", fields)

    def test_field_order_is_preserved(self):
        response = self.client.post(
            "/api/draft", json={"text": "Client cannot connect to the wireless network", "mode": "staff"}
        )
        self.assertEqual(
            list(response.get_json()["fields"].keys()),
            ["Title of Request", "Description of Request", "Comment", "Closing statement"],
        )

    def test_short_input_is_rejected(self):
        response = self.client.post("/api/draft", json={"text": "wifi"})
        self.assertEqual(response.status_code, 400)

    def test_home_page_renders(self):
        self.assertEqual(self.client.get("/").status_code, 200)


class ClaudeDrafter(unittest.TestCase):
    """The model writes wording; the rules keep the facts. The API is stubbed, so
    these run offline and prove the guardrails, not the model's prose."""

    def setUp(self):
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        self.drafter = Drafter(drafter="auto")
        self.reply = {
            "title": "Wireless: uofs-secure fails on Chromebook after reset",
            "description": "The client cannot join uofs-secure on a Chromebook after a password reset.",
            "steps": ["Removed the saved network", "Re-entered the identity with the domain"],
            "issue": "connecting your Chromebook to uofs-secure",
        }
        self.drafter._call_claude = lambda system, user: self.reply

    def tearDown(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)

    def draft(self, **kw):
        return self.drafter.draft(
            "Client cannot connect to uofs-secure on a Chromebook after a password reset", **kw
        )

    def test_auto_picks_claude_when_a_key_is_set(self):
        self.assertEqual(self.drafter.drafter, "claude")
        self.assertEqual(self.draft()["drafter"], "claude")

    def test_model_wording_is_used(self):
        result = self.draft(notes="removed saved network, confirmed connected")
        self.assertEqual(result["fields"]["Title of Request"], self.reply["title"])
        self.assertIn("connecting your Chromebook", result["fields"]["Closing statement"])

    def test_model_steps_not_in_the_article_are_flagged(self):
        comment = self.draft(notes="removed saved network")["fields"]["Comment"]
        self.assertIn("[CHECK] Removed the saved network", comment)

    def test_model_cannot_claim_a_resolution(self):
        # Notes do not confirm the fix, so the status line must stay unresolved
        # whatever the model writes.
        self.reply["steps"].append("The Client's problem was Successfully resolved.")
        result = self.draft(notes="advised them to try again at home")
        self.assertEqual(result["status"], "In Process")
        self.assertTrue(
            result["fields"]["Comment"].endswith("The resolution was not confirmed at the desk.")
        )

    def test_escalation_comment_is_not_rewritten(self):
        result = self.drafter.draft("Client clicked a phishing link and entered their password")
        self.assertTrue(result["escalate"])
        self.assertIn("Escalated rather than resolved", result["fields"]["Comment"])

    def test_student_mode_is_rewritten_too(self):
        result = self.draft(mode="student")
        self.assertEqual(result["fields"]["Title of Request"], self.reply["title"])
        self.assertNotIn("Comment", result["fields"])

    def test_api_failure_falls_back_to_the_template(self):
        def boom(system, user):
            raise ConnectionError("offline")

        self.drafter._call_claude = boom
        result = self.draft()
        self.assertEqual(result["drafter"], "template")
        self.assertTrue(any("failed" in flag for flag in result["flags"]))


class GeminiDrafter(unittest.TestCase):
    """Same guardrails, different provider."""

    def setUp(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ["GEMINI_API_KEY"] = "test-key"
        self.drafter = Drafter(drafter="auto")
        self.drafter._call_gemini = lambda system, user: {
            "title": "Printing: job denied for low credit",
            "description": "The client's print job was denied.",
            "steps": ["Added print credit"],
            "issue": "printing",
        }

    def tearDown(self):
        os.environ.pop("GEMINI_API_KEY", None)

    def test_auto_picks_gemini_with_only_a_gemini_key(self):
        self.assertEqual(self.drafter.drafter, "gemini")

    def test_gemini_wording_with_the_same_guardrails(self):
        result = self.drafter.draft("Print job denied at the library", notes="told them to add credit at home")
        self.assertEqual(result["drafter"], "gemini")
        self.assertEqual(result["fields"]["Title of Request"], "Printing: job denied for low credit")
        self.assertEqual(result["status"], "In Process")
        self.assertIn("[CHECK] Added print credit", result["fields"]["Comment"])


class GeminiBusy(unittest.TestCase):
    """The free tier returns 503 when it is busy; one busy signal must not fail a draft."""

    REPLY = {"title": "t", "description": "d", "steps": [], "issue": "i"}

    def setUp(self):
        import app.drafting as drafting

        self.drafting = drafting
        self.saved_backoff = drafting.BACKOFF_SECONDS
        drafting.BACKOFF_SECONDS = 0
        os.environ["GEMINI_API_KEY"] = "test-key"
        os.environ["TICKET_MAKER_MODEL"] = "first-model,second-model"
        self.drafter = Drafter(drafter="gemini")
        self.calls = []

    def tearDown(self):
        self.drafting.BACKOFF_SECONDS = self.saved_backoff
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("TICKET_MAKER_MODEL", None)

    def fails(self, code):
        import urllib.error

        return urllib.error.HTTPError("url", code, "busy", {}, None)

    def test_busy_then_answers_on_retry(self):
        def request(model, system, user):
            self.calls.append(model)
            if len(self.calls) == 1:
                raise self.fails(503)
            return self.REPLY

        self.drafter._gemini_request = request
        self.assertEqual(self.drafter._call_gemini("s", "u"), self.REPLY)
        self.assertEqual(self.calls, ["first-model", "first-model"])

    def test_falls_back_to_the_next_model(self):
        def request(model, system, user):
            self.calls.append(model)
            if model == "first-model":
                raise self.fails(503)
            return self.REPLY

        self.drafter._gemini_request = request
        self.drafter._call_gemini("s", "u")
        self.assertEqual(self.drafter.last_model, "second-model")

    def test_bad_key_is_not_retried(self):
        def request(model, system, user):
            self.calls.append(model)
            raise self.fails(400)

        self.drafter._gemini_request = request
        with self.assertRaises(Exception):
            self.drafter._call_gemini("s", "u")
        self.assertEqual(len(self.calls), 1)


class PlatformSteps(unittest.TestCase):
    """uofs-secure differs by device; the ticket must carry that device's steps."""

    def comment(self, text):
        return DRAFTER.draft(text, notes="connected successfully")["fields"]["Comment"]

    def test_chromebook_gets_the_full_identity(self):
        self.assertIn("abc123@usask.ca", self.comment("Client cannot connect to uofs-secure on a Chromebook"))

    def test_mac_gets_the_admin_password_step(self):
        self.assertIn("administrator username and password", self.comment("MacBook will not join uofs-secure"))

    def test_iphone_gets_the_trust_step(self):
        self.assertIn("selected Trust", self.comment("iPhone cannot connect to uofs-secure"))

    def test_windows_uses_the_nsid_alone(self):
        comment = self.comment("Windows laptop will not connect to uofs-secure")
        self.assertIn("NSID as the username", comment)
        self.assertNotIn("@usask.ca", comment)

    def test_no_device_falls_back_to_general_steps(self):
        comment = self.comment("Client cannot connect to uofs-secure")
        self.assertIn("in the format the device requires", comment)

    def test_remarkable_gets_its_own_steps(self):
        self.assertIn("hamburger menu", self.comment("reMarkable tablet will not join uofs-secure"))

    def test_machine_is_not_mistaken_for_mac(self):
        from app.drafting import _detect_platform

        self.assertIsNone(_detect_platform("the lab machine will not connect"))


class StaffGate(unittest.TestCase):
    """The gate is the server's, so hiding the form in the browser is irrelevant."""

    def setUp(self):
        os.environ["TICKET_MAKER_DESK_PASSWORD"] = "desk-secret"
        os.environ["TICKET_MAKER_SECRET_KEY"] = "test-key"
        self.client = create_app().test_client()

    def tearDown(self):
        os.environ.pop("TICKET_MAKER_DESK_PASSWORD", None)
        os.environ.pop("TICKET_MAKER_SECRET_KEY", None)

    def test_staff_draft_refused_without_password(self):
        response = self.client.post(
            "/api/draft", json={"text": "Client cannot connect to the wireless network"}
        )
        self.assertEqual(response.status_code, 401)

    def test_student_draft_still_open(self):
        response = self.client.post(
            "/api/draft",
            json={"text": "My laptop will not join the wifi", "mode": "student"},
        )
        self.assertEqual(response.status_code, 200)

    def test_wrong_password_rejected(self):
        self.assertEqual(self.client.post("/api/login", json={"password": "guess"}).status_code, 401)

    def test_staff_draft_allowed_after_login(self):
        self.client.post("/api/login", json={"password": "desk-secret"})
        response = self.client.post(
            "/api/draft", json={"text": "Client cannot connect to the wireless network"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Comment", response.get_json()["fields"])

    def test_logout_closes_the_gate_again(self):
        self.client.post("/api/login", json={"password": "desk-secret"})
        self.client.post("/api/logout")
        response = self.client.post(
            "/api/draft", json={"text": "Client cannot connect to the wireless network"}
        )
        self.assertEqual(response.status_code, 401)

    def test_session_never_returns_the_password(self):
        body = self.client.get("/api/session").get_data(as_text=True)
        self.assertNotIn("desk-secret", body)


class OpenByDefault(unittest.TestCase):
    def setUp(self):
        os.environ.pop("TICKET_MAKER_DESK_PASSWORD", None)
        self.client = create_app().test_client()

    def test_staff_draft_open_when_no_password_configured(self):
        response = self.client.post(
            "/api/draft", json={"text": "Client cannot connect to the wireless network"}
        )
        self.assertEqual(response.status_code, 200)

    def test_session_declares_the_unprotected_state(self):
        state = self.client.get("/api/session").get_json()
        self.assertFalse(state["password_required"])
        self.assertTrue(state["staff_authenticated"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
