# Copyright (c) 2026, Studo and Contributors
# For license information, please see license.txt
"""Provider error types and error-message extraction."""

import frappe
from frappe import _


class ProviderError(Exception):
    """A provider call failed. Carries a human-readable message and a title."""

    def __init__(self, message, title=None):
        super().__init__(message)
        self.message = message
        self.title = title or _("Error")


class UnsupportedFeatureError(ProviderError):
    """The active provider cannot express what the message asks for."""

    def __init__(self, feature, provider):
        self.feature = feature
        self.provider = provider
        super().__init__(
            _("{0} messages are not supported by the {1} provider.").format(
                str(feature).replace("_", " ").title(), provider
            ),
            title=_("Not supported by {0}").format(provider),
        )


def extract_integration_error(exc) -> tuple[str, str]:
    """Best-effort (message, title) from a failed `make_post_request`.

    The previous inline handler assumed `frappe.flags.integration_request` was
    always set and always JSON, so a DNS or connection failure raised an
    AttributeError inside the except block and masked the real error. Every
    guard here exists because of that.
    """
    request = getattr(frappe.flags, "integration_request", None)
    if request is None:
        return str(exc), _("Error")

    try:
        payload = request.json()
    except Exception:
        return str(exc), _("Error")

    if not isinstance(payload, dict):
        return str(exc), _("Error")

    # Meta: {"error": {"message": ..., "error_user_title": ...}}
    error = payload.get("error") or {}
    if isinstance(error, dict) and error:
        message = error.get("Error") or error.get("message") or str(exc)
        return message, error.get("error_user_title") or _("Error")

    # Infobip: {"requestError": {"serviceException": {"text": ..., "messageId": ...}}}
    service_exception = (payload.get("requestError") or {}).get("serviceException") or {}
    if isinstance(service_exception, dict) and service_exception:
        message = service_exception.get("text") or service_exception.get("messageId") or str(exc)
        return message, _("Infobip Error")

    return str(exc), _("Error")
