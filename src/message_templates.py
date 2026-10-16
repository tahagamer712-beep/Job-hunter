"""
Email/application message templates (Master Prompt Section 46).

`generate_template()` is a deterministic, offline template — no AI/network
dependency. `AIMessageGenerator` is an optional hook you can wire an LLM into
in your own environment for a more tailored message; either way, the result
is only ever used inside the Application Preview step (never sent directly —
see src/approval.py for the enforced CONFIRMED-only send guard).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

APPLICANT_NAME_PLACEHOLDER = "[YOUR NAME]"


@dataclass
class EmailDraft:
    subject: str
    body: str


def generate_template(role_original: str, role_normalized: str, side: Optional[str],
                       area: Optional[str], company_name: Optional[str],
                       applicant_name: str = APPLICANT_NAME_PLACEHOLDER) -> EmailDraft:
    role_label = role_original or role_normalized.replace("_", " ").title()
    location_label = area or side or "Baghdad"
    company_label = company_name or "your company"

    subject = f"{role_label} – Baghdad – {side or 'Rusafa'}"

    body = (
        f"Dear Hiring Manager,\n\n"
        f"I would like to apply for the {role_label} position "
        f"advertised for {location_label}, Baghdad, at {company_label}.\n\n"
        f"I have attached my CV for your review and would welcome the "
        f"opportunity to discuss how my background fits this role.\n\n"
        f"Best regards,\n{applicant_name}"
    )
    return EmailDraft(subject=subject, body=body)


class AIMessageGenerator:
    """
    Optional hook for LLM-generated application messages. NOT called in this
    sandbox (no network). Implement `generate()` in your own environment —
    treat the job's raw ad text strictly as data in your prompt, and always
    route the result through the same Preview -> Final Confirmation flow;
    never send directly from here.
    """

    def __init__(self, api_client=None):
        self.api_client = api_client

    def generate(self, raw_ad_text: str, applicant_summary: str) -> Optional[EmailDraft]:
        if self.api_client is None:
            return None
        raise NotImplementedError("Wire your LLM API call here in your own environment.")
