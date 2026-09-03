# Copyright (c) 2026, Studo and Contributors
# For license information, please see license.txt
"""The provider contract.

A provider owns *transport and wire format* for one WhatsApp integration. It
does not own business logic: parameter resolution, media URL resolution and the
`WhatsApp Message` lifecycle stay in the doctypes, so both providers behave
identically in everything a user can observe apart from the bytes on the wire.
"""

from abc import ABC, abstractmethod

from frappe_whatsapp.providers.errors import UnsupportedFeatureError
from frappe_whatsapp.providers.types import (
    DownloadedMedia,
    InboundBatch,
    InboundMediaRef,
    OutboundMessage,
    RemoteTemplate,
    SendResult,
    TemplateSyncResult,
)


class WhatsAppProvider(ABC):
    name: str = "unknown"
    capabilities: frozenset = frozenset()

    #: Whether the provider wants us to mint the message id and correlation
    #: token up front. Meta assigns its own; Infobip honours ours.
    wants_client_message_id: bool = False

    def __init__(self, account):
        self.account = account

    # ------------------------------------------------------------ capabilities

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def require(self, capability: str) -> None:
        """Fail before any HTTP call if the provider cannot express this."""
        if not self.supports(capability):
            raise UnsupportedFeatureError(capability, self.name)

    # ---------------------------------------------------------------- outbound

    @abstractmethod
    def send_message(self, msg: OutboundMessage) -> SendResult:
        """Send a free-form (non-template) message."""

    @abstractmethod
    def send_template(self, msg: OutboundMessage) -> SendResult:
        """Send a template message. `msg.template` is fully resolved."""

    def mark_read(self, provider_message_id: str) -> bool:
        raise UnsupportedFeatureError("read_receipt", self.name)

    # ----------------------------------------------------------------- inbound

    def parse_inbound(self, payload: dict) -> InboundBatch:
        """Turn a webhook body into normalized messages and status updates."""
        raise NotImplementedError(f"{self.name} does not implement parse_inbound yet")

    def authenticate_webhook(self, request) -> bool:
        """Verify a webhook request actually came from the provider."""
        return True

    def download_inbound_media(self, ref: InboundMediaRef) -> DownloadedMedia:
        """Fetch inbound media. Raises ProviderError on failure."""
        raise NotImplementedError(f"{self.name} does not implement media download yet")

    # --------------------------------------------------------------- templates

    def create_template(self, doc) -> TemplateSyncResult:
        raise UnsupportedFeatureError("template_crud", self.name)

    def update_template(self, doc) -> None:
        raise UnsupportedFeatureError("template_crud", self.name)

    def delete_template(self, doc) -> None:
        raise UnsupportedFeatureError("template_crud", self.name)

    def list_templates(self) -> list[RemoteTemplate]:
        raise UnsupportedFeatureError("template_fetch", self.name)

    def prepare_header_example(self, doc):
        """Return whatever the provider needs to reference the sample media.

        Meta uploads the bytes and returns an opaque handle; Infobip takes a
        public URL. Callers store the result and pass it back at create time.
        """
        return None

    # ------------------------------------------------------------- diagnostics

    @abstractmethod
    def test_connection(self) -> dict:
        """Cheap round-trip proving credentials work. For the settings UI."""
