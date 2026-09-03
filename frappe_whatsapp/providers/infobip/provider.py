# Copyright (c) 2026, Studo and Contributors
# For license information, please see license.txt
"""Infobip WhatsApp provider.

Credentials reuse the existing `WhatsApp Account` fields so account resolution,
the CRM's `is_whatsapp_enabled()` gate and provisioning keep working unchanged:

    url      -> https://<subdomain>.api.infobip.com
    token    -> API key, sent as `Authorization: App <key>`
    phone_id -> sender MSISDN, also the inbound routing key
"""

import json

import frappe
from frappe import _
from frappe.integrations.utils import make_get_request, make_post_request

from frappe_whatsapp.providers import capabilities
from frappe_whatsapp.providers.base import WhatsAppProvider
from frappe_whatsapp.providers.errors import ProviderError, extract_integration_error
from frappe_whatsapp.providers.infobip.payload import (
    build_freeform_payload,
    build_template_payload,
)
from frappe_whatsapp.providers.types import OutboundMessage, SendResult


class InfobipProvider(WhatsAppProvider):
    name = "Infobip"
    capabilities = capabilities.INFOBIP
    # Infobip honours a caller-supplied message id, which is what makes
    # delivery-report correlation deterministic instead of a lookup race.
    wants_client_message_id = True

    # ------------------------------------------------------------------ wiring

    @property
    def sender(self) -> str:
        return (self.account.phone_id or "").lstrip("+")

    @property
    def _base_url(self) -> str:
        return (self.account.url or "").rstrip("/")

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"App {self.account.get_password('token')}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _post(self, endpoint: str, payload: dict) -> dict:
        """POST to /whatsapp/1/message/<endpoint>."""
        url = f"{self._base_url}/whatsapp/1/message/{endpoint}"
        try:
            return make_post_request(url, headers=self._headers, data=json.dumps(payload))
        except Exception as exc:
            message, title = extract_integration_error(exc)
            self._log_failure(url, payload, message)
            raise ProviderError(message, title) from exc

    def _log_failure(self, url: str, payload: dict, message: str) -> None:
        request = getattr(frappe.flags, "integration_request", None)
        body = None
        if request is not None:
            try:
                body = request.json()
            except Exception:
                body = None
        try:
            frappe.get_doc(
                {
                    "doctype": "WhatsApp Notification Log",
                    "template": "Infobip Send Failed",
                    "meta_data": body or {"url": url, "error": message, "request": payload},
                }
            ).insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Infobip: could not write notification log")

    # ---------------------------------------------------------------- outbound

    def send_message(self, msg: OutboundMessage) -> SendResult:
        self.require(msg.content_type)
        endpoint, payload = build_freeform_payload(msg, self.sender)
        return self._to_result(self._post(endpoint, payload))

    def send_template(self, msg: OutboundMessage) -> SendResult:
        endpoint, payload = build_template_payload(msg, self.sender)
        return self._to_result(self._post(endpoint, payload))

    def _to_result(self, response: dict) -> SendResult:
        """Normalize the single and bulk response shapes.

        Non-template sends answer with the message info directly; the template
        endpoint wraps it in `{"bulkId", "messages": [...]}`.
        """
        info = response
        if isinstance(response.get("messages"), list) and response["messages"]:
            info = response["messages"][0]

        message_id = info.get("messageId")
        if not message_id:
            raise ProviderError(
                _("Infobip accepted the request but returned no message id."),
                _("Infobip Error"),
            )

        status = (info.get("status") or {}).get("groupName")
        if status in ("REJECTED", "UNDELIVERABLE"):
            description = (info.get("status") or {}).get("description") or status
            raise ProviderError(description, _("Infobip rejected the message"))

        return SendResult(message_id=message_id, status="Success", raw=response)

    def mark_read(self, provider_message_id: str) -> bool:
        self.require("read_receipt")
        url = (
            f"{self._base_url}/whatsapp/1/senders/{self.sender}"
            f"/message/{provider_message_id}/read"
        )
        try:
            make_post_request(url, headers=self._headers, data=json.dumps({}))
        except Exception as exc:
            message, title = extract_integration_error(exc)
            raise ProviderError(message, title) from exc
        return True

    # ------------------------------------------------------------- diagnostics

    def test_connection(self) -> dict:
        """Read the sender's business profile back. Cheap and read-only."""
        url = f"{self._base_url}/whatsapp/1/senders/{self.sender}/business-info"
        try:
            response = make_get_request(url, headers=self._headers)
        except Exception as exc:
            message, title = extract_integration_error(exc)
            return {"ok": False, "error": message, "title": title}

        return {
            "ok": True,
            "sender": self.sender,
            "verified_name": response.get("businessName") or response.get("about"),
        }
