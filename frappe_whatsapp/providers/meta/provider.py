# Copyright (c) 2026, Studo and Contributors
# For license information, please see license.txt
"""Meta Cloud API provider.

Transport lifted verbatim from `WhatsAppMessage.notify()` and
`send_read_receipt()`. The only intentional change is error extraction, which
now goes through `extract_integration_error` so a connection failure no longer
raises AttributeError inside the except block and masks the real cause.
"""

import json

import frappe
from frappe.integrations.utils import make_get_request, make_post_request

from frappe_whatsapp.providers import capabilities
from frappe_whatsapp.providers.base import WhatsAppProvider
from frappe_whatsapp.providers.errors import ProviderError, extract_integration_error
from frappe_whatsapp.providers.meta.payload import (
    build_freeform_payload,
    build_template_payload,
)
from frappe_whatsapp.providers.types import OutboundMessage, SendResult


class MetaCloudProvider(WhatsAppProvider):
    name = "Meta Cloud API"
    capabilities = capabilities.META_CLOUD_API
    wants_client_message_id = False

    # ------------------------------------------------------------------ wiring

    @property
    def _messages_url(self) -> str:
        account = self.account
        return f"{account.url}/{account.version}/{account.phone_id}/messages"

    @property
    def _headers(self) -> dict:
        return {
            "authorization": f"Bearer {self.account.get_password('token')}",
            "content-type": "application/json",
        }

    def _post(self, data: dict) -> dict:
        """POST to the messages endpoint, logging and re-raising on failure."""
        try:
            return make_post_request(
                self._messages_url,
                headers=self._headers,
                data=json.dumps(data),
            )
        except Exception as exc:
            message, title = extract_integration_error(exc)
            request = getattr(frappe.flags, "integration_request", None)
            meta_data = None
            if request is not None:
                try:
                    meta_data = request.json()
                except Exception:
                    meta_data = None
            frappe.get_doc(
                {
                    "doctype": "WhatsApp Notification Log",
                    "template": "Text Message",
                    "meta_data": meta_data or {"error": str(exc)},
                }
            ).insert(ignore_permissions=True)
            raise ProviderError(message, title) from exc

    # ---------------------------------------------------------------- outbound

    def send_message(self, msg: OutboundMessage) -> SendResult:
        return self._send(build_freeform_payload(msg))

    def send_template(self, msg: OutboundMessage) -> SendResult:
        return self._send(build_template_payload(msg))

    def send_raw(self, data: dict) -> SendResult:
        """Escape hatch for callers holding a pre-built Meta payload."""
        return self._send(data)

    def _send(self, data: dict) -> SendResult:
        response = self._post(data)
        return SendResult(
            message_id=response["messages"][0]["id"],
            status="Success",
            raw=response,
        )

    def mark_read(self, provider_message_id: str) -> bool:
        self.require("read_receipt")
        response = self._post(
            {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": provider_message_id,
            }
        )
        return bool(response.get("success"))

    # ------------------------------------------------------------- diagnostics

    def test_connection(self) -> dict:
        """Read the phone number back from the Graph API."""
        account = self.account
        try:
            response = make_get_request(
                f"{account.url}/{account.version}/{account.phone_id}",
                headers={"authorization": f"Bearer {account.get_password('token')}"},
                params={"fields": "display_phone_number,verified_name"},
            )
        except Exception as exc:
            message, title = extract_integration_error(exc)
            return {"ok": False, "error": message, "title": title}

        return {
            "ok": True,
            "phone_number": response.get("display_phone_number"),
            "verified_name": response.get("verified_name"),
        }
