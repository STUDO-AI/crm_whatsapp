# Copyright (c) 2026, Studo and Contributors
# For license information, please see license.txt
"""Normalized data structures exchanged between the doctypes and the providers.

These are a *projection of the `WhatsApp Message` doctype*, deliberately not an
abstract model of WhatsApp. Keeping them close to the doc is what lets the Meta
payload builders be a verbatim lift of the previous inline code, which in turn
is what makes the provider refactor provably behavior-preserving.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ButtonParam:
    """A template button that carries a runtime parameter."""

    index: str
    sub_type: str  # quick_reply | url | mpm
    value: Any


@dataclass
class TemplateSpec:
    """A template send with every parameter already resolved.

    Parameter resolution (`body_param` -> `flags.custom_ref_doc` -> ref doc
    fields) is shared business logic and stays in the doctype; providers only
    ever see resolved values.
    """

    name: str
    language_code: str
    body_params: list[str] = field(default_factory=list)
    header_type: str | None = None
    header_media_url: str | None = None
    header_filename: str | None = None
    buttons: list[ButtonParam] = field(default_factory=list)
    product_catalog: dict | None = None


@dataclass
class FlowSpec:
    """WhatsApp Flow interactive message. Meta only."""

    flow_id: str
    screen: str | None
    cta: str
    token: str
    mode: str | None = None


@dataclass
class OutboundMessage:
    """Everything a provider needs to send one message."""

    to: str
    content_type: str
    body: str | None = None
    media_url: str | None = None
    media_filename: str | None = None
    is_reply: bool = False
    reply_to_message_id: str | None = None
    buttons: list[dict] | None = None
    template: TemplateSpec | None = None
    flow: FlowSpec | None = None
    # Provider-assigned correlation handles. Meta ignores both; Infobip echoes
    # `callback_data` back on the delivery report and honours `client_message_id`
    # as the message id, which is what makes DLR correlation deterministic.
    client_message_id: str | None = None
    callback_data: str | None = None
    source_doc: str | None = None

    @property
    def is_template(self) -> bool:
        return self.template is not None


@dataclass
class SendResult:
    message_id: str
    status: str = "Success"
    raw: dict = field(default_factory=dict)


@dataclass
class InboundMediaRef:
    """How to fetch inbound media. Meta gives an id (2 hops), Infobip a url (1)."""

    provider_media_id: str | None = None
    url: str | None = None
    mime_type: str | None = None
    filename: str | None = None


@dataclass
class DownloadedMedia:
    content: bytes
    filename: str
    mime_type: str | None = None


@dataclass
class InboundMessage:
    provider_message_id: str
    from_number: str
    content_type: str
    body: str | None = None
    profile_name: str | None = None
    is_reply: bool = False
    reply_to_message_id: str | None = None
    media: InboundMediaRef | None = None
    flow_response: dict | None = None
    product_catalog: dict | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class StatusUpdate:
    """A normalized delivery/read event.

    `status` is always one of sent | delivered | read | failed, so the CRM's
    tick rendering keeps working regardless of provider.
    """

    provider_message_id: str
    status: str
    conversation_id: str | None = None
    error: dict | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class InboundBatch:
    """One webhook delivery, parsed."""

    account: str | None = None
    messages: list[InboundMessage] = field(default_factory=list)
    statuses: list[StatusUpdate] = field(default_factory=list)
    template_statuses: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class RemoteTemplate:
    """A template as the provider reports it, ready to upsert locally."""

    remote_id: str | None
    name: str
    language_code: str
    status: str
    category: str | None = None
    body: str | None = None
    body_examples: list[str] = field(default_factory=list)
    header_type: str | None = None
    header_text: str | None = None
    footer: str | None = None
    buttons: list[dict] = field(default_factory=list)


@dataclass
class TemplateSyncResult:
    remote_id: str | None
    status: str
