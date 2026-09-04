# Copyright (c) 2026, Studo and Contributors
# See license.txt
"""Outbound payload tests for the Infobip provider.

Asserts the envelope and per-endpoint content shapes against the contract in
Infobip's own SDK: content type selects the endpoint, `messageId` is ours to
choose, and `callbackData` carries the account back to us on the delivery
report.
"""

import json
from unittest.mock import patch

import frappe

from frappe_whatsapp.providers import get_provider
from frappe_whatsapp.providers.errors import UnsupportedFeatureError
from frappe_whatsapp.providers.infobip.provider import InfobipProvider
from frappe_whatsapp.testing import IntegrationTestCase

ACCOUNT = "Infobip Payload Account"
SENDER = "553199990001"
TO = "919900112288"


class InfobipPayloadTestCase(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._ensure_account()

    @classmethod
    def _ensure_account(cls):
        if not frappe.db.exists("WhatsApp Account", ACCOUNT):
            frappe.get_doc(
                {
                    "doctype": "WhatsApp Account",
                    "account_name": ACCOUNT,
                    "provider": "Infobip",
                    "status": "Active",
                    "url": "https://1e91zk.api.infobip.com",
                    "phone_id": SENDER,
                    "webhook_verify_token": "infobip_payload_token",
                }
            ).insert(ignore_permissions=True)
            frappe.db.commit()  # nosemgrep: frappe-manual-commit -- fixture must outlive the transaction

    def setUp(self):
        from frappe.utils.password import set_encrypted_password

        set_encrypted_password("WhatsApp Account", ACCOUNT, "infobip_key_123", "token")

    def build(self, **fields):
        doc = frappe.new_doc("WhatsApp Message")
        doc.update({"type": "Outgoing", "to": TO, "whatsapp_account": ACCOUNT, **fields})
        return doc

    def capture(self, doc, template=False):
        """Return (endpoint, payload) the provider would POST."""
        with patch.object(InfobipProvider, "_post", autospec=True) as post:
            post.return_value = {
                "messages": [{"messageId": "infobip-1", "status": {"groupName": "PENDING"}}]
            }
            if template:
                doc.send_template()
            else:
                doc.send_outgoing()
        self.assertEqual(post.call_count, 1)
        _self, endpoint, payload = post.call_args[0]
        return endpoint, payload


class TestInfobipProviderSelection(InfobipPayloadTestCase):
    def test_account_resolves_to_infobip_provider(self):
        provider = get_provider(ACCOUNT)
        self.assertIsInstance(provider, InfobipProvider)
        self.assertEqual(provider.name, "Infobip")

    def test_sender_strips_leading_plus(self):
        account = frappe.get_doc("WhatsApp Account", ACCOUNT)
        account.phone_id = f"+{SENDER}"
        self.assertEqual(InfobipProvider(account).sender, SENDER)

    def test_reaction_is_rejected_before_any_http_call(self):
        doc = self.build(content_type="reaction", message="👍", reply_to_message_id="x")
        with patch.object(InfobipProvider, "_post", autospec=True) as post:
            with self.assertRaises(Exception) as ctx:
                doc.send_outgoing()
        post.assert_not_called()
        self.assertIn("reaction", str(ctx.exception).lower())

    def test_unsupported_feature_names_the_provider(self):
        provider = get_provider(ACCOUNT)
        with self.assertRaises(UnsupportedFeatureError) as ctx:
            provider.require("flow")
        self.assertIn("Infobip", str(ctx.exception))


class TestInfobipFreeformPayloads(InfobipPayloadTestCase):
    def test_text_endpoint_and_envelope(self):
        # No URL in the body: `previewUrl` must be omitted, otherwise Infobip
        # rejects the send with 400 "content: must contain a previewable URL".
        endpoint, payload = self.capture(self.build(content_type="text", message="Olá"))
        self.assertEqual(endpoint, "text")
        self.assertEqual(payload["from"], SENDER)
        self.assertEqual(payload["to"], TO)
        self.assertEqual(payload["content"], {"text": "Olá"})
        self.assertNotIn("previewUrl", payload["content"])

    def test_text_with_url_enables_preview(self):
        _endpoint, payload = self.capture(
            self.build(content_type="text", message="veja https://studoflow.com.br")
        )
        self.assertTrue(payload["content"]["previewUrl"])

    def test_client_message_id_and_callback_data_are_set(self):
        _endpoint, payload = self.capture(self.build(content_type="text", message="oi"))
        self.assertTrue(payload["messageId"])
        self.assertEqual(payload["callbackData"], ACCOUNT)

    def test_reply_uses_context_reply_to_message_id(self):
        _endpoint, payload = self.capture(
            self.build(
                content_type="text",
                message="re",
                is_reply=1,
                reply_to_message_id="infobip-original",
            )
        )
        self.assertEqual(payload["context"], {"replyToMessageId": "infobip-original"})

    def test_image_uses_media_url_and_caption(self):
        endpoint, payload = self.capture(
            self.build(content_type="image", message="legenda", attach="/files/p.png")
        )
        self.assertEqual(endpoint, "image")
        self.assertEqual(
            payload["content"],
            {"mediaUrl": f"{frappe.utils.get_url()}/files/p.png", "caption": "legenda"},
        )

    def test_document_carries_filename(self):
        endpoint, payload = self.capture(
            self.build(content_type="document", message="contrato", attach="/files/c.pdf")
        )
        self.assertEqual(endpoint, "document")
        self.assertEqual(payload["content"]["filename"], "c.pdf")

    def test_audio_is_sent_as_voice_and_has_no_caption(self):
        endpoint, payload = self.capture(
            self.build(content_type="audio", message="ignorado", attach="/files/a.ogg")
        )
        self.assertEqual(endpoint, "audio")
        self.assertEqual(payload["content"]["voice"], True)
        self.assertNotIn("caption", payload["content"])

    def test_interactive_buttons_endpoint(self):
        buttons = [{"id": "b1", "title": "Sim"}, {"id": "b2", "title": "Não"}]
        endpoint, payload = self.capture(
            self.build(content_type="interactive", message="Confirma?", buttons=json.dumps(buttons))
        )
        self.assertEqual(endpoint, "interactive/buttons")
        self.assertEqual(
            payload["content"]["action"]["buttons"][0],
            {"type": "REPLY", "id": "b1", "title": "Sim"},
        )

    def test_interactive_switches_to_list_above_three(self):
        buttons = [{"id": f"b{i}", "title": f"O{i}"} for i in range(1, 6)]
        endpoint, payload = self.capture(
            self.build(content_type="interactive", message="Escolha", buttons=json.dumps(buttons))
        )
        self.assertEqual(endpoint, "interactive/list")
        self.assertEqual(len(payload["content"]["action"]["sections"][0]["rows"]), 5)


class TestInfobipTemplatePayloads(InfobipPayloadTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = cls._template("infobip_tpl", sample_values="first_name")
        cls.template_image = cls._template(
            "infobip_tpl_img",
            sample_values=None,
            header_type="IMAGE",
            sample="/files/head.png",
        )

    @classmethod
    def _template(cls, name, **overrides):
        docname = f"{name}-en"
        if frappe.db.exists("WhatsApp Templates", docname):
            return docname
        values = {
            "doctype": "WhatsApp Templates",
            "name": docname,
            "template_name": name,
            "actual_name": name,
            "template": "Hello {{1}}",
            "category": "UTILITY",
            "language": frappe.db.get_value("Language", {"language_code": "en"}) or "en",
            "language_code": "en",
            "whatsapp_account": ACCOUNT,
            "status": "APPROVED",
            "id": f"{name}_id",
        }
        values.update(overrides)
        frappe.get_doc(values).db_insert()
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- fixture must outlive the transaction
        return docname

    def test_template_is_wrapped_in_a_bulk_messages_array(self):
        doc = self.build(
            message_type="Template",
            template=self.template,
            body_param=json.dumps({"1": "Maria"}),
        )
        endpoint, payload = self.capture(doc, template=True)
        self.assertEqual(endpoint, "template")
        self.assertIn("messages", payload)
        self.assertEqual(len(payload["messages"]), 1)

    def test_template_placeholders_are_positional_strings(self):
        doc = self.build(
            message_type="Template",
            template=self.template,
            body_param=json.dumps({"1": "Maria"}),
        )
        _endpoint, payload = self.capture(doc, template=True)
        content = payload["messages"][0]["content"]
        self.assertEqual(content["templateName"], "infobip_tpl")
        self.assertEqual(content["language"], "en")
        self.assertEqual(content["templateData"]["body"], {"placeholders": ["Maria"]})

    def test_template_image_header(self):
        doc = self.build(message_type="Template", template=self.template_image)
        _endpoint, payload = self.capture(doc, template=True)
        header = payload["messages"][0]["content"]["templateData"]["header"]
        self.assertEqual(
            header,
            {"type": "IMAGE", "mediaUrl": f"{frappe.utils.get_url()}/files/head.png"},
        )

    def test_bulk_response_message_id_is_extracted(self):
        doc = self.build(
            message_type="Template",
            template=self.template,
            body_param=json.dumps({"1": "X"}),
        )
        with patch.object(InfobipProvider, "_post", autospec=True) as post:
            post.return_value = {
                "bulkId": "bulk-1",
                "messages": [{"messageId": "msg-abc", "status": {"groupName": "PENDING"}}],
            }
            doc.send_template()
        self.assertEqual(doc.message_id, "msg-abc")


class TestInfobipTemplateCreatePayload(IntegrationTestCase):
    """Unit tests for the `POST .../templates` body builder (pure function)."""

    def _doc(self, **overrides):
        doc = frappe._dict(
            {
                "actual_name": "nova_data",
                "template_name": "Nova data",
                "language_code": "pt_BR",
                "category": "MARKETING",
                "template": "Olá {{1}}, novidade!",
                "sample_values": "João",
                "header_type": None,
                "header": None,
                "sample": None,
                "footer": None,
                "buttons": [],
            }
        )
        doc.update(overrides)
        return doc

    def test_minimal_text_template(self):
        from frappe_whatsapp.providers.infobip.payload import build_template_create_payload

        payload = build_template_create_payload(self._doc())
        self.assertEqual(payload["name"], "nova_data")
        self.assertEqual(payload["language"], "pt_BR")
        self.assertEqual(payload["category"], "MARKETING")
        self.assertEqual(
            payload["structure"]["body"],
            {"text": "Olá {{1}}, novidade!", "examples": ["João"]},
        )
        self.assertNotIn("header", payload["structure"])

    def test_image_header_uses_media_url(self):
        from frappe_whatsapp.providers.infobip.payload import build_template_create_payload

        payload = build_template_create_payload(
            self._doc(header_type="IMAGE", sample="/files/x.png"),
            header_media_url="https://cdn.example.com/x.png",
        )
        self.assertEqual(
            payload["structure"]["header"],
            {"format": "IMAGE", "mediaUrl": "https://cdn.example.com/x.png"},
        )

    def test_footer_and_buttons(self):
        from frappe_whatsapp.providers.infobip.payload import build_template_create_payload

        doc = self._doc(
            footer="Studo Flow",
            buttons=[
                frappe._dict({"button_type": "Quick Reply", "button_label": "Sim"}),
                frappe._dict(
                    {
                        "button_type": "Visit Website",
                        "button_label": "Abrir",
                        "website_url": "https://studoflow.com.br",
                    }
                ),
                frappe._dict(
                    {
                        "button_type": "Call Phone",
                        "button_label": "Ligar",
                        "phone_number": "+5511999999999",
                    }
                ),
            ],
        )
        structure = build_template_create_payload(doc)["structure"]
        self.assertEqual(structure["footer"], {"text": "Studo Flow"})
        self.assertEqual(
            structure["buttons"],
            [
                {"type": "QUICK_REPLY", "text": "Sim"},
                {"type": "URL", "text": "Abrir", "url": "https://studoflow.com.br"},
                {"type": "PHONE_NUMBER", "text": "Ligar", "phoneNumber": "+5511999999999"},
            ],
        )
