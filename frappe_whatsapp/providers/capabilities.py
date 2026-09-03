# Copyright (c) 2026, Studo and Contributors
# For license information, please see license.txt
"""What each provider can express.

Used both to fail fast with an actionable error before any HTTP call, and to
let the CRM hide affordances the active provider cannot honour (a reaction
button is worse than useless on Infobip).
"""

# Free-form content types plus the capabilities the doctypes gate on.
META_CLOUD_API = frozenset(
    {
        "text",
        "image",
        "document",
        "video",
        "audio",
        "reaction",
        "interactive",
        "flow",
        "order",
        "mpm",
        "read_receipt",
        "template_crud",
        "template_fetch",
        "template_media_upload",
    }
)

# Infobip exposes no outbound reaction endpoint, and Flows / order / multi-product
# messages are Meta-native concepts its message API does not proxy.
INFOBIP = frozenset(
    {
        "text",
        "image",
        "document",
        "video",
        "audio",
        "sticker",
        "interactive",
        "read_receipt",
        "template_crud",
        "template_fetch",
    }
)

BY_PROVIDER = {
    "Meta Cloud API": META_CLOUD_API,
    "Infobip": INFOBIP,
}

DEFAULT_PROVIDER = "Meta Cloud API"


def get_capabilities(provider: str | None) -> frozenset:
    return BY_PROVIDER.get(provider or DEFAULT_PROVIDER, frozenset())
