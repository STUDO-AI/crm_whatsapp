# Copyright (c) 2026, Studo and Contributors
# For license information, please see license.txt
"""Turn a `WhatsApp Message.attach` value into a URL the provider can fetch.

Both Meta and Infobip fetch outbound media over plain HTTPS with no credentials
of ours, so whatever we hand them has to be publicly readable. That makes this
one helper load-bearing for both providers.
"""

import frappe


def resolve_public_media_url(attach: str | None, share_doc=None) -> str | None:
    """Absolute, publicly fetchable URL for `attach`.

    Passes absolute URLs through untouched. Relative Frappe file URLs are made
    absolute against the site URL. Private files additionally get a document
    share key appended, since the provider fetches them anonymously.
    """
    if not attach:
        return None

    if attach.startswith("http"):
        return attach

    url = f"{frappe.utils.get_url()}{attach}"

    if attach.startswith("/private/") and share_doc is not None:
        key = _share_key(share_doc)
        if key:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}key={key}"

    return url


def _share_key(doc) -> str | None:
    """Document share key for `doc`, or None if it cannot be produced."""
    getter = getattr(doc, "get_document_share_key", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), "WhatsApp: could not create media share key"
        )
        return None
