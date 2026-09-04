# Copyright (c) 2026, Studo and Contributors
# For license information, please see license.txt
"""Infobip WhatsApp wire format.

Infobip splits by content type at the URL (`/whatsapp/1/message/text`,
`.../image`, ...) rather than carrying a `type` discriminator in the body, so
each builder returns `(endpoint, payload)`.

Two envelope fields have no Meta equivalent and are worth knowing about:
`messageId` is ours to choose, which makes delivery-report correlation
deterministic, and `callbackData` is echoed back on the report, which is how we
find our way back to the sending account.
"""

import re

from frappe_whatsapp.providers.types import OutboundMessage

# Infobip rejects `previewUrl: true` on a text that has no previewable URL
# ("content: must contain a previewable URL", HTTP 400), so we only enable the
# link preview when the body actually carries an http(s) URL.
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _has_previewable_url(text: str | None) -> bool:
    return bool(text) and bool(_URL_RE.search(text))


# content_type -> endpoint suffix under /whatsapp/1/message/
MEDIA_ENDPOINTS = {
    "image": "image",
    "document": "document",
    "video": "video",
    "audio": "audio",
    "sticker": "sticker",
}


def _envelope(msg: OutboundMessage, sender: str, content: dict) -> dict:
    payload = {
        "from": sender,
        "to": msg.to,
        "content": content,
    }
    if msg.client_message_id:
        payload["messageId"] = msg.client_message_id
    if msg.callback_data:
        payload["callbackData"] = msg.callback_data
    if msg.is_reply and msg.reply_to_message_id:
        payload["context"] = {"replyToMessageId": msg.reply_to_message_id}
    return payload


def build_freeform_payload(msg: OutboundMessage, sender: str) -> tuple[str, dict]:
    """Return (endpoint, payload) for a non-template message."""
    content_type = msg.content_type

    if content_type == "text":
        content = {"text": msg.body}
        if _has_previewable_url(msg.body):
            content["previewUrl"] = True
        return "text", _envelope(msg, sender, content)

    if content_type in MEDIA_ENDPOINTS:
        content = {"mediaUrl": msg.media_url}
        # Infobip rejects a null caption, and audio/sticker take none at all.
        if content_type in ("image", "document", "video") and msg.body:
            content["caption"] = msg.body
        if content_type == "document" and msg.media_filename:
            content["filename"] = msg.media_filename
        if content_type == "audio":
            # Renders as a voice note rather than a file attachment.
            content["voice"] = True
        return MEDIA_ENDPOINTS[content_type], _envelope(msg, sender, content)

    if content_type == "interactive":
        buttons = msg.buttons or []
        if isinstance(buttons, list) and len(buttons) > 3:
            content = {
                "body": {"text": msg.body},
                "action": {
                    "title": "Select Option",
                    "sections": [
                        {
                            "title": "Options",
                            "rows": [
                                {
                                    "id": button["id"],
                                    "title": button["title"],
                                    "description": button.get("description", ""),
                                }
                                for button in buttons[:10]
                            ],
                        }
                    ],
                },
            }
            return "interactive/list", _envelope(msg, sender, content)

        content = {
            "body": {"text": msg.body},
            "action": {
                "buttons": [
                    {"type": "REPLY", "id": button["id"], "title": button["title"]}
                    for button in buttons[:3]
                ]
            },
        }
        return "interactive/buttons", _envelope(msg, sender, content)

    raise ValueError(f"Unsupported Infobip content type: {content_type}")


def build_template_payload(msg: OutboundMessage, sender: str) -> tuple[str, dict]:
    """Return (endpoint, payload) for a template send.

    The template endpoint is a bulk one: it takes `{"messages": [...]}` and
    answers with `{"bulkId", "messages": [...]}`.
    """
    spec = msg.template

    template_data: dict = {"body": {"placeholders": [str(p or "") for p in spec.body_params]}}

    if spec.header_type and spec.header_media_url:
        if spec.header_type == "IMAGE":
            template_data["header"] = {"type": "IMAGE", "mediaUrl": spec.header_media_url}
        elif spec.header_type == "DOCUMENT":
            template_data["header"] = {
                "type": "DOCUMENT",
                "mediaUrl": spec.header_media_url,
                "filename": spec.header_filename,
            }
        elif spec.header_type == "VIDEO":
            template_data["header"] = {"type": "VIDEO", "mediaUrl": spec.header_media_url}

    if spec.buttons:
        # Infobip takes buttons positionally, in template order, rather than by
        # the explicit index Meta wants.
        template_data["buttons"] = [
            {
                "type": "QUICK_REPLY" if button.sub_type == "quick_reply" else "URL",
                "parameter": str(button.value or ""),
            }
            for button in spec.buttons
            if button.sub_type in ("quick_reply", "url")
        ]

    content = {
        "templateName": spec.name,
        "templateData": template_data,
        "language": spec.language_code,
    }

    return "template", {"messages": [_envelope(msg, sender, content)]}


# ------------------------------------------------------- template management (v2)
#
# Creating/registering a template is a different API from sending one: Infobip
# carries the template shape in a `structure` object (header/body/footer/buttons)
# under `POST /whatsapp/2/senders/{sender}/templates`, and takes header media as a
# public `mediaUrl` rather than an uploaded handle (unlike Meta's resumable upload).

_TEMPLATE_BUTTON_TYPE = {
    "Quick Reply": "QUICK_REPLY",
    "Visit Website": "URL",
    "Call Phone": "PHONE_NUMBER",
}


def build_template_create_payload(doc, header_media_url: str | None = None) -> dict:
    """Body for `POST /whatsapp/2/senders/{sender}/templates`.

    `doc` is the `WhatsApp Templates` document; `header_media_url` is the already
    resolved public URL of the sample media for an IMAGE/DOCUMENT/VIDEO header.
    """
    structure: dict = {"body": _template_body(doc)}

    header = _template_header(doc, header_media_url)
    if header:
        structure["header"] = header
    if doc.get("footer"):
        structure["footer"] = {"text": doc.footer}
    buttons = _template_buttons(doc)
    if buttons:
        structure["buttons"] = buttons

    return {
        "name": _template_name(doc),
        "language": doc.language_code,
        "category": doc.category,
        "structure": structure,
    }


def _template_name(doc) -> str:
    return (doc.get("actual_name") or (doc.get("template_name") or "")).lower().replace(" ", "_")


def _template_body(doc) -> dict:
    body: dict = {"text": doc.template}
    examples = _template_csv(doc.get("sample_values"))
    if examples:
        body["examples"] = examples
    return body


def _template_header(doc, media_url: str | None) -> dict | None:
    header_type = doc.get("header_type")
    if not header_type:
        return None
    if header_type == "TEXT":
        header: dict = {"format": "TEXT", "text": doc.get("header") or ""}
        examples = [s for s in (doc.get("sample") or "").split(", ") if s]
        if examples:
            header["examples"] = examples
        return header
    header = {"format": header_type}
    if media_url:
        header["mediaUrl"] = media_url
    return header


def _template_buttons(doc) -> list[dict]:
    buttons = []
    for btn in doc.get("buttons") or []:
        infobip_type = _TEMPLATE_BUTTON_TYPE.get(btn.button_type)
        if not infobip_type:
            continue
        item: dict = {"type": infobip_type, "text": btn.button_label}
        if infobip_type == "URL":
            item["url"] = btn.website_url
        elif infobip_type == "PHONE_NUMBER":
            item["phoneNumber"] = btn.phone_number
        buttons.append(item)
    return buttons


def _template_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]
