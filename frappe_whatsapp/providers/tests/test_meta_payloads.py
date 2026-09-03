# Copyright (c) 2026, Studo and Contributors
# See license.txt
"""Golden payload tests for the Meta Cloud API outbound path.

These freeze the exact JSON that `WhatsAppMessage.send_outgoing()` and
`send_template()` hand to `notify()` TODAY, before the provider abstraction is
introduced. The provider refactor must keep every one of these byte-identical;
if a fixture here needs editing, the Meta path changed and that change must be
deliberate.

Where a fixture asserts something that looks wrong, it is deliberate: fixing a
bug in the send path must show up here as a reviewable fixture change rather
than silent drift.
"""

import json
from unittest.mock import patch

import frappe

from frappe_whatsapp.providers.meta.provider import MetaCloudProvider
from frappe_whatsapp.testing import IntegrationTestCase

ACCOUNT = "Golden Payload Account"
TO = "919900112299"


class MetaPayloadGoldenTestCase(IntegrationTestCase):
    """Builds outgoing messages and captures the payload sent to notify()."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._ensure_account()
        cls._ensure_templates()

    @classmethod
    def _ensure_account(cls):
        if frappe.db.exists("WhatsApp Account", ACCOUNT):
            return
        frappe.get_doc(
            {
                "doctype": "WhatsApp Account",
                "account_name": ACCOUNT,
                "status": "Active",
                "url": "https://graph.facebook.com",
                "version": "v17.0",
                "phone_id": "golden_phone_id",
                "business_id": "golden_business_id",
                "app_id": "golden_app_id",
                "webhook_verify_token": "golden_verify_token",
            }
        ).insert(ignore_permissions=True)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- fixture must outlive the transaction

    @classmethod
    def _template(cls, name, **overrides):
        """db_insert a template, bypassing the Meta round-trip in after_insert."""
        docname = f"{name}-en"
        if frappe.db.exists("WhatsApp Templates", docname):
            return docname
        values = {
            "doctype": "WhatsApp Templates",
            "name": docname,
            "template_name": name,
            "actual_name": name,
            "template": "Hello {{1}}",
            "category": "TRANSACTIONAL",
            "language": frappe.db.get_value("Language", {"language_code": "en"}) or "en",
            "language_code": "en",
            "whatsapp_account": ACCOUNT,
            "status": "APPROVED",
            "id": f"{name}_id",
        }
        values.update(overrides)
        buttons = values.pop("_buttons", None)
        doc = frappe.get_doc(values)
        doc.db_insert()
        for idx, btn in enumerate(buttons or [], start=1):
            child = frappe.get_doc(
                {
                    "doctype": "WhatsApp Button",
                    "parent": docname,
                    "parenttype": "WhatsApp Templates",
                    "parentfield": "buttons",
                    "idx": idx,
                    **btn,
                }
            )
            child.db_insert()
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- fixture must outlive the transaction
        return docname

    @classmethod
    def _ensure_templates(cls):
        cls.tpl_plain = cls._template("golden_plain", sample_values=None)
        cls.tpl_params = cls._template("golden_params", sample_values="first_name")
        cls.tpl_image = cls._template(
            "golden_image", sample_values=None, header_type="IMAGE", sample="/files/sample.png"
        )
        cls.tpl_document = cls._template(
            "golden_doc", sample_values=None, header_type="DOCUMENT", sample="/files/contract.pdf"
        )
        cls.tpl_quick_reply = cls._template(
            "golden_qr",
            sample_values=None,
            _buttons=[{"button_type": "Quick Reply", "button_label": "Yes"}],
        )
        cls.tpl_dynamic_url = cls._template(
            "golden_url",
            sample_values=None,
            _buttons=[
                {
                    "button_type": "Visit Website",
                    "button_label": "Open",
                    "url_type": "Dynamic",
                    "website_url": "full_name",
                }
            ],
        )

    # ------------------------------------------------------------------ helpers

    def build(self, **fields):
        doc = frappe.new_doc("WhatsApp Message")
        doc.update({"type": "Outgoing", "to": TO, "whatsapp_account": ACCOUNT, **fields})
        return doc

    def capture(self, doc, template=False):
        """Run the send path with the HTTP boundary stubbed; return the payload.

        Stubbing `MetaCloudProvider._post` rather than the transport library
        keeps this pinned to the last point where the payload is fully built,
        which is exactly what these tests are about.
        """
        with patch.object(MetaCloudProvider, "_post", autospec=True) as post:
            post.return_value = {"messages": [{"id": "wamid.TEST"}]}
            if template:
                doc.send_template()
            else:
                doc.send_outgoing()
        self.assertEqual(post.call_count, 1, "the provider must POST exactly once")
        return post.call_args[0][1]

    @property
    def site_url(self):
        return frappe.utils.get_url()


class TestMetaFreeformPayloads(MetaPayloadGoldenTestCase):
    def test_text(self):
        payload = self.capture(self.build(content_type="text", message="Olá mundo"))
        self.assertEqual(
            payload,
            {
                "messaging_product": "whatsapp",
                "to": TO,
                "type": "text",
                "text": {"preview_url": True, "body": "Olá mundo"},
            },
        )

    def test_text_with_reply_context(self):
        payload = self.capture(
            self.build(
                content_type="text",
                message="respondendo",
                is_reply=1,
                reply_to_message_id="wamid.ORIGINAL",
            )
        )
        self.assertEqual(payload["context"], {"message_id": "wamid.ORIGINAL"})

    def test_reply_context_omitted_without_message_id(self):
        payload = self.capture(
            self.build(content_type="text", message="oi", is_reply=1, reply_to_message_id=None)
        )
        self.assertNotIn("context", payload)

    def test_image_link_is_joined_without_a_double_slash(self):
        """`attach` already starts with "/", so the site URL is joined bare.

        This asserted "//files/..." until the shared media resolver landed;
        both send paths now agree.
        """
        payload = self.capture(
            self.build(content_type="image", message="legenda", attach="/files/pic.png")
        )
        self.assertEqual(
            payload["image"],
            {"link": f"{self.site_url}/files/pic.png", "caption": "legenda"},
        )

    def test_image_absolute_url_passthrough(self):
        payload = self.capture(
            self.build(
                content_type="image", message="cap", attach="https://cdn.example.com/a.png"
            )
        )
        self.assertEqual(
            payload["image"], {"link": "https://cdn.example.com/a.png", "caption": "cap"}
        )

    def test_document(self):
        payload = self.capture(
            self.build(content_type="document", message="contrato", attach="/files/c.pdf")
        )
        self.assertEqual(payload["type"], "document")
        self.assertEqual(
            payload["document"],
            {
                "link": f"{self.site_url}/files/c.pdf",
                "caption": "contrato",
            },
        )

    def test_video(self):
        payload = self.capture(
            self.build(content_type="video", message="clipe", attach="/files/v.mp4")
        )
        self.assertEqual(
            payload["video"], {"link": f"{self.site_url}/files/v.mp4", "caption": "clipe"}
        )

    def test_audio_has_no_caption(self):
        payload = self.capture(
            self.build(content_type="audio", message="ignorado", attach="/files/a.ogg")
        )
        self.assertEqual(payload["audio"], {"link": f"{self.site_url}/files/a.ogg"})

    def test_reaction(self):
        payload = self.capture(
            self.build(content_type="reaction", message="👍", reply_to_message_id="wamid.TARGET")
        )
        self.assertEqual(
            payload["reaction"], {"message_id": "wamid.TARGET", "emoji": "👍"}
        )

    def test_interactive_buttons_three_or_fewer(self):
        buttons = [{"id": "b1", "title": "Sim"}, {"id": "b2", "title": "Não"}]
        payload = self.capture(
            self.build(content_type="interactive", message="Confirma?", buttons=json.dumps(buttons))
        )
        self.assertEqual(payload["type"], "interactive")
        self.assertEqual(
            payload["interactive"],
            {
                "type": "button",
                "body": {"text": "Confirma?"},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": "b1", "title": "Sim"}},
                        {"type": "reply", "reply": {"id": "b2", "title": "Não"}},
                    ]
                },
            },
        )

    def test_interactive_switches_to_list_above_three(self):
        buttons = [{"id": f"b{i}", "title": f"Opção {i}"} for i in range(1, 5)]
        payload = self.capture(
            self.build(content_type="interactive", message="Escolha", buttons=json.dumps(buttons))
        )
        interactive = payload["interactive"]
        self.assertEqual(interactive["type"], "list")
        self.assertEqual(interactive["action"]["button"], "Select Option")
        self.assertEqual(interactive["action"]["sections"][0]["title"], "Options")
        self.assertEqual(len(interactive["action"]["sections"][0]["rows"]), 4)
        self.assertEqual(
            interactive["action"]["sections"][0]["rows"][0],
            {"id": "b1", "title": "Opção 1", "description": ""},
        )

    def test_interactive_list_caps_at_ten_rows(self):
        buttons = [{"id": f"b{i}", "title": f"O{i}"} for i in range(1, 15)]
        payload = self.capture(
            self.build(content_type="interactive", message="x", buttons=json.dumps(buttons))
        )
        self.assertEqual(len(payload["interactive"]["action"]["sections"][0]["rows"]), 10)

    def test_non_outgoing_is_noop(self):
        doc = self.build(type="Incoming", content_type="text", message="oi")
        with patch.object(MetaCloudProvider, "_post", autospec=True) as post:
            doc.send_outgoing()
        post.assert_not_called()


class TestMetaTemplatePayloads(MetaPayloadGoldenTestCase):
    def test_template_without_params_still_sends_empty_body_component(self):
        doc = self.build(message_type="Template", template=self.tpl_plain)
        payload = self.capture(doc, template=True)
        self.assertEqual(
            payload,
            {
                "messaging_product": "whatsapp",
                "to": TO,
                "type": "template",
                "template": {
                    "name": "golden_plain",
                    "language": {"code": "en"},
                    "components": [{"type": "body", "parameters": []}],
                },
            },
        )

    def test_template_body_params_from_body_param(self):
        doc = self.build(
            message_type="Template",
            template=self.tpl_params,
            body_param=json.dumps({"1": "Maria"}),
        )
        payload = self.capture(doc, template=True)
        self.assertEqual(
            payload["template"]["components"][0],
            {"type": "body", "parameters": [{"type": "text", "text": "Maria"}]},
        )
        self.assertEqual(json.loads(doc.template_parameters), ["Maria"])

    def test_template_body_params_from_custom_ref_doc(self):
        doc = self.build(message_type="Template", template=self.tpl_params)
        doc.flags.custom_ref_doc = {"first_name": "Joana"}
        payload = self.capture(doc, template=True)
        self.assertEqual(
            payload["template"]["components"][0]["parameters"],
            [{"type": "text", "text": "Joana"}],
        )

    def test_template_image_header_uses_attach(self):
        doc = self.build(
            message_type="Template", template=self.tpl_image, attach="/files/override.png"
        )
        payload = self.capture(doc, template=True)
        header = payload["template"]["components"][1]
        self.assertEqual(
            header,
            {
                "type": "header",
                "parameters": [
                    {"type": "image", "image": {"link": f"{self.site_url}/files/override.png"}}
                ],
            },
        )

    def test_template_image_header_falls_back_to_sample(self):
        """The campaign path: no `attach`, so template.sample must be used."""
        doc = self.build(message_type="Template", template=self.tpl_image)
        payload = self.capture(doc, template=True)
        self.assertEqual(
            payload["template"]["components"][1]["parameters"][0]["image"],
            {"link": f"{self.site_url}/files/sample.png"},
        )

    def test_template_document_header_derives_filename(self):
        doc = self.build(message_type="Template", template=self.tpl_document)
        payload = self.capture(doc, template=True)
        self.assertEqual(
            payload["template"]["components"][1]["parameters"][0]["document"],
            {"link": f"{self.site_url}/files/contract.pdf", "filename": "contract.pdf"},
        )

    def test_template_header_url_has_no_double_slash(self):
        """Header media joins WITHOUT an inserted "/", unlike freeform media.

        This asymmetry with `test_image_link_has_double_slash` is real and must
        survive the refactor.
        """
        doc = self.build(message_type="Template", template=self.tpl_image)
        payload = self.capture(doc, template=True)
        link = payload["template"]["components"][1]["parameters"][0]["image"]["link"]
        self.assertNotIn("//files", link)

    def test_template_mpm_component(self):
        catalog = {"thumbnail_product_retailer_id": "SKU1", "sections": []}
        doc = self.build(
            message_type="Template",
            template=self.tpl_plain,
            product_catalog_json=json.dumps(catalog),
        )
        payload = self.capture(doc, template=True)
        self.assertEqual(
            payload["template"]["components"][1],
            {
                "type": "button",
                "sub_type": "mpm",
                "index": "0",
                "parameters": [{"type": "action", "action": catalog}],
            },
        )

    def test_template_quick_reply_button(self):
        doc = self.build(message_type="Template", template=self.tpl_quick_reply)
        payload = self.capture(doc, template=True)
        self.assertEqual(
            payload["template"]["components"][1],
            {
                "type": "button",
                "sub_type": "quick_reply",
                "index": "0",
                "parameters": [{"type": "payload", "payload": "Yes"}],
            },
        )

    def test_template_button_index_shifts_when_mpm_present(self):
        doc = self.build(
            message_type="Template",
            template=self.tpl_quick_reply,
            product_catalog_json=json.dumps({"sections": []}),
        )
        payload = self.capture(doc, template=True)
        subtypes = {c.get("sub_type"): c.get("index") for c in payload["template"]["components"][1:]}
        self.assertEqual(subtypes, {"mpm": "0", "quick_reply": "1"})

    def test_template_dynamic_url_button_resolves_from_ref_doc(self):
        doc = self.build(
            message_type="Template",
            template=self.tpl_dynamic_url,
            reference_doctype="User",
            reference_name="Administrator",
        )
        payload = self.capture(doc, template=True)
        button = payload["template"]["components"][1]
        self.assertEqual(button["sub_type"], "url")
        self.assertEqual(button["index"], "0")
        self.assertEqual(button["parameters"][0]["type"], "text")

    def test_template_name_prefers_actual_name(self):
        doc = self.build(message_type="Template", template=self.tpl_plain)
        payload = self.capture(doc, template=True)
        self.assertEqual(payload["template"]["name"], "golden_plain")
