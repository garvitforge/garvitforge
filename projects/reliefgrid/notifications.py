"""Opt-in Twilio delivery for ReliefGrid critical-incident alerts."""
from __future__ import annotations

import base64
import html
import os
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any

E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


class TwilioNotifier:
    """Minimal Twilio REST client using only the Python standard library."""

    def __init__(self, account_sid: str, auth_token: str, from_number: str, recipients: tuple[str, ...]) -> None:
        self.account_sid = account_sid
        self._auth_token = auth_token
        self.from_number = from_number
        self.recipients = recipients

    @classmethod
    def from_environment(cls) -> TwilioNotifier | None:
        enabled = os.environ.get("RELIEFGRID_ALERTS_ENABLED", "").strip().lower()
        if enabled not in {"1", "true", "yes"}:
            return None

        account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
        from_number = os.environ.get("TWILIO_FROM_NUMBER", "").strip()
        recipients = tuple(
            number.strip()
            for number in os.environ.get("RELIEFGRID_ALERT_RECIPIENTS", "").split(",")
            if number.strip()
        )
        if (
            not re.fullmatch(r"AC[0-9a-fA-F]{32}", account_sid)
            or not auth_token
            or not E164_PATTERN.fullmatch(from_number)
            or not recipients
            or any(not E164_PATTERN.fullmatch(number) for number in recipients)
        ):
            return None
        return cls(account_sid, auth_token, from_number, recipients)

    def _post(self, resource: str, fields: dict[str, str]) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/{resource}.json"
        authorization = base64.b64encode(f"{self.account_sid}:{self._auth_token}".encode("utf-8")).decode("ascii")
        request = Request(
            url,
            data=urlencode(fields).encode("utf-8"),
            headers={
                "Authorization": f"Basic {authorization}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Twilio returned HTTP {response.status}.")

    @staticmethod
    def _summary(incident: dict[str, Any]) -> str:
        return (
            f"ReliefGrid critical incident {incident['code']}: "
            f"{incident['kind']} in {incident['district']}."
        )

    def send_sms(self, incident: dict[str, Any]) -> None:
        body = f"{self._summary(incident)} Check the ReliefGrid dashboard and acknowledge the report."
        for recipient in self.recipients:
            self._post(
                "Messages",
                {"To": recipient, "From": self.from_number, "Body": body},
            )

    def place_call(self, incident: dict[str, Any]) -> None:
        spoken_message = (
            f"{self._summary(incident)} "
            "This report has not been acknowledged. Please check the ReliefGrid dashboard."
        )
        twiml = f"<Response><Say>{html.escape(spoken_message)}</Say></Response>"
        for recipient in self.recipients:
            self._post(
                "Calls",
                {"To": recipient, "From": self.from_number, "Twiml": twiml},
            )