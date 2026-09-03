# Copyright (c) 2026, Studo and Contributors
# For license information, please see license.txt
"""Provider registry.

`WhatsApp Account.provider` selects the transport. Existing rows default to
Meta Cloud API, so adding this layer changes nothing until an Infobip account
is created and made the default outgoing account.
"""

import frappe
from frappe import _

from frappe_whatsapp.providers.base import WhatsAppProvider
from frappe_whatsapp.providers.capabilities import DEFAULT_PROVIDER, get_capabilities
from frappe_whatsapp.providers.errors import ProviderError, UnsupportedFeatureError

__all__ = [
    "WhatsAppProvider",
    "ProviderError",
    "UnsupportedFeatureError",
    "get_capabilities",
    "get_provider",
]


def _registry() -> dict:
    """Imported lazily so this module stays importable without a site."""
    from frappe_whatsapp.providers.meta.provider import MetaCloudProvider

    registry = {MetaCloudProvider.name: MetaCloudProvider}

    try:
        from frappe_whatsapp.providers.infobip.provider import InfobipProvider
    except ImportError:
        pass
    else:
        registry[InfobipProvider.name] = InfobipProvider

    return registry


def get_provider(account) -> WhatsAppProvider:
    """Provider instance for a `WhatsApp Account` name or document."""
    if isinstance(account, str):
        account = frappe.get_cached_doc("WhatsApp Account", account)

    provider_name = account.get("provider") or DEFAULT_PROVIDER
    provider_class = _registry().get(provider_name)
    if not provider_class:
        frappe.throw(
            _("Unknown WhatsApp provider {0} on account {1}.").format(
                provider_name, account.name
            )
        )

    return provider_class(account)
