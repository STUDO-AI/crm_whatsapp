# Copyright (c) 2026, Studo and Contributors
# For license information, please see license.txt
"""Meta Cloud API wire format.

Lifted verbatim from `WhatsAppMessage.send_outgoing()` / `send_template()`.
`self.X` became `msg.X`; nothing else was changed — no renaming, no cleanup, no
reordering of dict keys or component appends. The golden tests in
`providers/tests/test_meta_payloads.py` exist to keep it that way.
"""

from frappe_whatsapp.providers.types import OutboundMessage


def build_freeform_payload(msg: OutboundMessage) -> dict:
    """Non-template outgoing message."""
    link = msg.media_url

    data = {
        "messaging_product": "whatsapp",
        "to": msg.to,
        "type": msg.content_type,
    }
    if msg.is_reply and msg.reply_to_message_id:
        data["context"] = {"message_id": msg.reply_to_message_id}

    if msg.content_type in ["document", "image", "video"]:
        data[msg.content_type.lower()] = {
            "link": link,
            "caption": msg.body,
        }
    elif msg.content_type == "reaction":
        data["reaction"] = {
            "message_id": msg.reply_to_message_id,
            "emoji": msg.body,
        }
    elif msg.content_type == "text":
        data["text"] = {"preview_url": True, "body": msg.body}

    elif msg.content_type == "audio":
        data["audio"] = {"link": link}

    elif msg.content_type == "interactive":
        data["type"] = "interactive"
        buttons_data = msg.buttons

        if isinstance(buttons_data, list) and len(buttons_data) > 3:
            # Use list message for more than 3 options (max 10)
            data["interactive"] = {
                "type": "list",
                "body": {"text": msg.body},
                "action": {
                    "button": "Select Option",
                    "sections": [
                        {
                            "title": "Options",
                            "rows": [
                                {
                                    "id": btn["id"],
                                    "title": btn["title"],
                                    "description": btn.get("description", ""),
                                }
                                for btn in buttons_data[:10]
                            ],
                        }
                    ],
                },
            }
        else:
            # Use button message for 3 or fewer options
            data["interactive"] = {
                "type": "button",
                "body": {"text": msg.body},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": btn["id"], "title": btn["title"]}}
                        for btn in buttons_data[:3]
                    ]
                },
            }

    elif msg.content_type == "flow":
        flow = msg.flow
        data["type"] = "interactive"
        data["interactive"] = {
            "type": "flow",
            "body": {"text": msg.body or "Please fill out the form"},
            "action": {
                "name": "flow",
                "parameters": {
                    "flow_message_version": "3",
                    "flow_id": flow.flow_id,
                    "flow_cta": flow.cta,
                    "flow_action": "navigate",
                    "flow_action_payload": {"screen": flow.screen},
                },
            },
        }

        # Add draft mode for testing unpublished flows
        if flow.mode:
            data["interactive"]["action"]["parameters"]["mode"] = flow.mode

        # Add flow token - generate one if not provided (required by WhatsApp)
        data["interactive"]["action"]["parameters"]["flow_token"] = flow.token

    return data


def build_template_payload(msg: OutboundMessage) -> dict:
    """Template outgoing message. `msg.template` carries resolved values."""
    spec = msg.template
    data = {
        "messaging_product": "whatsapp",
        "to": msg.to,
        "type": "template",
        "template": {
            "name": spec.name,
            "language": {"code": spec.language_code},
            "components": [],
        },
    }

    parameters = [{"type": "text", "text": value} for value in spec.body_params]

    # Always add the body component, even if parameters list is empty
    data["template"]["components"].append(
        {
            "type": "body",
            "parameters": parameters,
        }
    )

    if spec.header_type and spec.header_media_url:
        url = spec.header_media_url
        if spec.header_type == "IMAGE":
            data["template"]["components"].append(
                {
                    "type": "header",
                    "parameters": [{"type": "image", "image": {"link": url}}],
                }
            )
        elif spec.header_type == "DOCUMENT":
            data["template"]["components"].append(
                {
                    "type": "header",
                    "parameters": [
                        {
                            "type": "document",
                            "document": {"link": url, "filename": spec.header_filename},
                        }
                    ],
                }
            )

    # We check this before standard buttons because MPM is an interactive action
    if spec.product_catalog is not None:
        data["template"]["components"].append(
            {
                "type": "button",
                "sub_type": "mpm",
                "index": "0",
                "parameters": [{"type": "action", "action": spec.product_catalog}],
            }
        )

    button_parameters = []
    for button in spec.buttons:
        if button.sub_type == "quick_reply":
            button_parameters.append(
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": button.index,
                    "parameters": [{"type": "payload", "payload": button.value}],
                }
            )
        elif button.sub_type == "url":
            button_parameters.append(
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": button.index,
                    "parameters": [{"type": "text", "text": button.value}],
                }
            )

    if button_parameters:
        data["template"]["components"].extend(button_parameters)

    return data
