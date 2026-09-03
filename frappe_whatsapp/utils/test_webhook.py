# Copyright (c) 2025, Shridhar Patil and Contributors
# See license.txt

import json
from unittest.mock import patch, MagicMock, PropertyMock

import frappe
from frappe_whatsapp.testing import IntegrationTestCase

from frappe_whatsapp.utils.webhook import (
    infobip,
    _is_allowed_infobip_media_url,
    update_message_status,
    update_status,
    update_template_status,
)


class TestWebhookHelpers(IntegrationTestCase):
    """Tests for webhook helper functions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._ensure_test_account()

    @classmethod
    def _ensure_test_account(cls):
        if not frappe.db.exists("WhatsApp Account", "Test WA Webhook Account"):
            account = frappe.get_doc({
                "doctype": "WhatsApp Account",
                "account_name": "Test WA Webhook Account",
                "status": "Active",
                "url": "https://graph.facebook.com",
                "version": "v17.0",
                "phone_id": "webhook_test_phone_id",
                "business_id": "webhook_test_business_id",
                "app_id": "webhook_test_app_id",
                "webhook_verify_token": "webhook_test_verify_token",
                "is_default_incoming": 1,
                "is_default_outgoing": 1,
            })
            account.insert(ignore_permissions=True)
            from frappe.utils.password import set_encrypted_password
            set_encrypted_password("WhatsApp Account", account.name, "test_webhook_token", "token")
            frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture must be visible to later queries

    def setUp(self):
        # Set password within each test's transaction scope
        from frappe.utils.password import set_encrypted_password
        set_encrypted_password("WhatsApp Account", "Test WA Webhook Account", "test_webhook_token", "token")

    def tearDown(self):
        for name in frappe.get_all("WhatsApp Message", filters={"message_id": ["like", "wamid.webhook_%"]}, pluck="name"):
            frappe.delete_doc("WhatsApp Message", name, force=True)
        for name in frappe.get_all("WhatsApp Notification Log", filters={"template": "Webhook"}, pluck="name"):
            frappe.delete_doc("WhatsApp Notification Log", name, force=True)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture must be visible to later queries

    def test_update_status_template_status(self):
        """Test update_status routes to template status update."""
        data = {
            "field": "message_template_status_update",
            "value": {
                "event": "APPROVED",
                "message_template_id": "999999",
            }
        }
        # Should not raise
        update_status(data)

    def test_update_message_status(self):
        """Test update_message_status updates WhatsApp Message status."""
        # Create a message first
        msg = frappe.get_doc({
            "doctype": "WhatsApp Message",
            "type": "Outgoing",
            "to": "919900112233",
            "message": "Status test",
            "message_id": "wamid.webhook_status_test",
            "content_type": "text",
            "whatsapp_account": "Test WA Webhook Account",
        })
        msg.flags.ignore_validate = True
        msg.db_insert()
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture must be visible to later queries

        data = {
            "statuses": [{
                "id": "wamid.webhook_status_test",
                "status": "delivered",
                "conversation": {"id": "conv_123"}
            }]
        }
        update_message_status(data)

        msg.reload()
        self.assertEqual(msg.status, "delivered")
        self.assertEqual(msg.conversation_id, "conv_123")

    def test_update_message_status_without_conversation(self):
        """Test update_message_status when no conversation ID."""
        msg = frappe.get_doc({
            "doctype": "WhatsApp Message",
            "type": "Outgoing",
            "to": "919900112234",
            "message": "No conv test",
            "message_id": "wamid.webhook_no_conv",
            "content_type": "text",
            "whatsapp_account": "Test WA Webhook Account",
        })
        msg.flags.ignore_validate = True
        msg.db_insert()
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture must be visible to later queries

        data = {
            "statuses": [{
                "id": "wamid.webhook_no_conv",
                "status": "sent",
            }]
        }
        update_message_status(data)

        msg.reload()
        self.assertEqual(msg.status, "sent")

    def test_update_template_status(self):
        """Test update_template_status updates template status via SQL."""
        # Create a template directly
        template_name = "test_webhook_tmpl-en"
        if not frappe.db.exists("WhatsApp Templates", template_name):
            doc = frappe.get_doc({
                "doctype": "WhatsApp Templates",
                "template_name": "test_webhook_tmpl",
                "actual_name": "test_webhook_tmpl",
                "template": "Webhook test template",
                "category": "TRANSACTIONAL",
                "language": frappe.db.get_value("Language", {"language_code": "en"}) or "en",
                "language_code": "en",
                "whatsapp_account": "Test WA Webhook Account",
                "status": "PENDING",
                "id": "webhook_tmpl_id_123",
            })
            doc.db_insert()
            frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture must be visible to later queries

        data = {
            "event": "APPROVED",
            "message_template_id": "webhook_tmpl_id_123",
        }
        update_template_status(data)

        status = frappe.db.get_value("WhatsApp Templates", {"id": "webhook_tmpl_id_123"}, "status")
        self.assertEqual(status, "APPROVED")


class TestInfobipWebhookEndpoint(IntegrationTestCase):
    """Tests for the Infobip webhook endpoint."""

    ACCOUNT = "Test Infobip Webhook Account"
    SENDER = "5511955031024"
    KEY = "infobip_webhook_key"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("WhatsApp Account", cls.ACCOUNT):
            account = frappe.get_doc({
                "doctype": "WhatsApp Account",
                "account_name": cls.ACCOUNT,
                "provider": "Infobip",
                "status": "Active",
                "url": "https://1e91zk.api.infobip.com",
                "phone_id": cls.SENDER,
                "webhook_verify_token": cls.KEY,
                "is_default_incoming": 1,
                "is_default_outgoing": 1,
            })
            account.insert(ignore_permissions=True)
            from frappe.utils.password import set_encrypted_password
            set_encrypted_password("WhatsApp Account", account.name, "infobip_test_key", "token")
            frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture must be visible to later queries

    def setUp(self):
        from frappe.utils.password import set_encrypted_password

        set_encrypted_password("WhatsApp Account", self.ACCOUNT, "infobip_test_key", "token")
        frappe.local.form_dict = frappe._dict({"key": self.KEY})

    def tearDown(self):
        for name in frappe.get_all("WhatsApp Message", filters={"message_id": ["like", "infobip_webhook_%"]}, pluck="name"):
            frappe.delete_doc("WhatsApp Message", name, force=True)
        for name in frappe.get_all("WhatsApp Notification Log", filters={"template": ["in", ["Infobip Webhook", "Infobip Webhook Error"]]}, pluck="name"):
            frappe.delete_doc("WhatsApp Notification Log", name, force=True)
        for name in frappe.get_all("File", filters={"attached_to_doctype": "WhatsApp Message"}, pluck="name"):
            frappe.delete_doc("File", name, force=True)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- clean shared test fixtures

    def _set_payload(self, payload):
        frappe.local.form_dict = frappe._dict({"key": self.KEY})
        mock_request = MagicMock()
        mock_request.json = payload
        return patch("frappe_whatsapp.utils.webhook.frappe.request", mock_request)

    def _message_payload(self, message):
        return {
            "results": [{
                "from": "553199990001",
                "to": self.SENDER,
                "receivedAt": "2026-09-03T12:00:00Z",
                "messageId": "infobip_webhook_text_1",
                "message": message,
                "contact": {"profileName": "Lead Test"},
            }],
            "messageCount": 1,
            "pendingMessageCount": 0,
        }

    def test_infobip_endpoint_exists(self):
        self.assertTrue(callable(infobip))

    def test_infobip_rejects_invalid_key(self):
        frappe.local.form_dict = frappe._dict({"key": "wrong"})
        with self.assertRaises(frappe.ValidationError):
            infobip()

    def test_infobip_text_message_creates_incoming_message(self):
        payload = self._message_payload({"type": "TEXT", "text": "Oi CRM"})
        with self._set_payload(payload):
            response = infobip()

        self.assertEqual(response, {"success": True})
        doc = frappe.get_doc("WhatsApp Message", {"message_id": "infobip_webhook_text_1"})
        self.assertEqual(doc.type, "Incoming")
        self.assertEqual(doc.get("from"), "553199990001")
        self.assertEqual(doc.message, "Oi CRM")
        self.assertEqual(doc.content_type, "text")
        self.assertEqual(doc.whatsapp_account, self.ACCOUNT)

    def test_infobip_button_reply_creates_button_message(self):
        payload = self._message_payload({
            "type": "INTERACTIVE_BUTTON_REPLY",
            "id": "confirmar",
            "title": "Confirmar",
        })
        payload["results"][0]["messageId"] = "infobip_webhook_button_1"

        with self._set_payload(payload):
            infobip()

        doc = frappe.get_doc("WhatsApp Message", {"message_id": "infobip_webhook_button_1"})
        self.assertEqual(doc.content_type, "button")
        self.assertEqual(doc.message, "confirmar")

    def test_infobip_delivery_report_updates_message_status(self):
        msg = frappe.get_doc({
            "doctype": "WhatsApp Message",
            "type": "Outgoing",
            "to": "553199990001",
            "message": "status",
            "message_id": "infobip_webhook_status_1",
            "content_type": "text",
            "whatsapp_account": self.ACCOUNT,
            "status": "sent",
        })
        msg.flags.ignore_validate = True
        msg.db_insert()
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- status callback reads this row

        payload = {
            "results": [{
                "messageId": "infobip_webhook_status_1",
                "to": "553199990001",
                "status": {"groupName": "DELIVERED"},
            }]
        }
        with self._set_payload(payload):
            infobip()

        msg.reload()
        self.assertEqual(msg.status, "delivered")

    def test_infobip_media_is_downloaded_and_attached(self):
        payload = self._message_payload({
            "type": "IMAGE",
            "caption": "Foto",
            "url": "https://1e91zk.api.infobip.com/whatsapp/1/senders/5511955031024/media/abc",
        })
        payload["results"][0]["messageId"] = "infobip_webhook_media_1"
        media_response = MagicMock()
        media_response.status_code = 200
        media_response.content = b"image-bytes"
        media_response.headers = {"Content-Type": "image/png"}

        with self._set_payload(payload), patch("frappe_whatsapp.utils.webhook.requests.get", return_value=media_response) as get:
            infobip()

        get.assert_called_once()
        self.assertEqual(get.call_args.kwargs["headers"], {"Authorization": "App infobip_test_key"})
        doc = frappe.get_doc("WhatsApp Message", {"message_id": "infobip_webhook_media_1"})
        self.assertEqual(doc.content_type, "image")
        self.assertTrue(doc.attach)

    def test_infobip_blocks_media_download_to_untrusted_host(self):
        payload = self._message_payload({
            "type": "IMAGE",
            "caption": "blocked",
            "url": "https://example.com/media",
        })
        payload["results"][0]["messageId"] = "infobip_webhook_blocked_media_1"

        with self._set_payload(payload), patch("frappe_whatsapp.utils.webhook.requests.get") as get:
            infobip()

        get.assert_not_called()
        doc = frappe.get_doc("WhatsApp Message", {"message_id": "infobip_webhook_blocked_media_1"})
        self.assertFalse(doc.attach)

    def test_infobip_invalid_result_is_logged_without_failing_the_webhook(self):
        payload = {"results": [{"message": "not-a-dict", "messageId": "infobip_webhook_bad_1"}]}
        with self._set_payload(payload):
            response = infobip()

        self.assertEqual(response, {"success": True})
        self.assertTrue(frappe.db.exists("WhatsApp Notification Log", {"template": "Infobip Webhook Error"}))

    def test_infobip_media_url_allowlist(self):
        account = frappe.get_doc("WhatsApp Account", self.ACCOUNT)
        self.assertTrue(_is_allowed_infobip_media_url("https://1e91zk.api.infobip.com/media", account))
        self.assertTrue(_is_allowed_infobip_media_url("https://api.infobip.com/media", account))
        self.assertFalse(_is_allowed_infobip_media_url("http://1e91zk.api.infobip.com/media", account))
        self.assertFalse(_is_allowed_infobip_media_url("https://evil.example/media", account))


class TestWebhookEndpoint(IntegrationTestCase):
    """Tests for the webhook endpoint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("WhatsApp Account", "Test WA Webhook EP Account"):
            account = frappe.get_doc({
                "doctype": "WhatsApp Account",
                "account_name": "Test WA Webhook EP Account",
                "status": "Active",
                "url": "https://graph.facebook.com",
                "version": "v17.0",
                "phone_id": "webhook_ep_phone_id",
                "business_id": "webhook_ep_business_id",
                "app_id": "webhook_ep_app_id",
                "webhook_verify_token": "Test WA Webhook EP Account",
                "is_default_incoming": 1,
                "is_default_outgoing": 1,
            })
            account.insert(ignore_permissions=True)
            from frappe.utils.password import set_encrypted_password
            set_encrypted_password("WhatsApp Account", account.name, "ep_token", "token")
            frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture must be visible to later queries

    def setUp(self):
        # Set password within each test's transaction scope
        from frappe.utils.password import set_encrypted_password
        set_encrypted_password("WhatsApp Account", "Test WA Webhook EP Account", "ep_token", "token")
        # Clear ALL defaults then set ours (db.set_value bypasses on_update hooks)
        frappe.db.sql("UPDATE `tabWhatsApp Account` SET is_default_outgoing=0, is_default_incoming=0")
        frappe.db.set_value("WhatsApp Account", "Test WA Webhook EP Account", {
            "is_default_outgoing": 1,
            "is_default_incoming": 1,
        })

    def tearDown(self):
        for name in frappe.get_all("WhatsApp Message", filters={"message_id": ["like", "wamid.webhook_ep_%"]}, pluck="name"):
            frappe.delete_doc("WhatsApp Message", name, force=True)
        for name in frappe.get_all("WhatsApp Notification Log", filters={"template": "Webhook"}, pluck="name"):
            frappe.delete_doc("WhatsApp Notification Log", name, force=True)
        for name in frappe.get_all("WhatsApp Profiles", filters={"number": ["like", "9199%"]}, pluck="name"):
            frappe.delete_doc("WhatsApp Profiles", name, force=True)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture must be visible to later queries

    def _make_mock_request(self, method="GET"):
        """Create a mock request object."""
        mock_request = MagicMock()
        mock_request.method = method
        return mock_request

    def test_webhook_get_verification(self):
        """Test GET webhook verification."""
        mock_request = self._make_mock_request("GET")
        frappe.local.form_dict = frappe._dict({
            "hub.challenge": "test_challenge_123",
            "hub.verify_token": "Test WA Webhook EP Account",
            "hub.mode": "subscribe",
        })

        with patch("frappe_whatsapp.utils.webhook.frappe.request", mock_request):
            from frappe_whatsapp.utils.webhook import webhook
            response = webhook()
            self.assertEqual(response.status_code, 200)

    def test_webhook_get_wrong_token(self):
        """Test GET webhook with wrong verify token."""
        mock_request = self._make_mock_request("GET")
        frappe.local.form_dict = frappe._dict({
            "hub.challenge": "test_challenge",
            "hub.verify_token": "wrong_token",
        })

        with patch("frappe_whatsapp.utils.webhook.frappe.request", mock_request):
            from frappe_whatsapp.utils.webhook import webhook
            with self.assertRaises(frappe.ValidationError):
                webhook()

    def test_webhook_post_text_message(self):
        """Test POST webhook with incoming text message."""
        mock_request = self._make_mock_request("POST")
        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "metadata": {"phone_number_id": "webhook_ep_phone_id"},
                        "contacts": [{"profile": {"name": "Test Sender"}}],
                        "messages": [{
                            "from": "919900112288",
                            "id": "wamid.webhook_ep_text_1",
                            "type": "text",
                            "text": {"body": "Hello from webhook test"},
                        }]
                    }
                }]
            }]
        }
        frappe.local.form_dict = frappe._dict(payload)

        with patch("frappe_whatsapp.utils.webhook.frappe.request", mock_request):
            from frappe_whatsapp.utils.webhook import webhook
            webhook()

        self.assertTrue(
            frappe.db.exists("WhatsApp Message", {"message_id": "wamid.webhook_ep_text_1"})
        )
        msg = frappe.get_doc("WhatsApp Message", {"message_id": "wamid.webhook_ep_text_1"})
        self.assertEqual(msg.type, "Incoming")
        self.assertEqual(msg.message, "Hello from webhook test")
        self.assertEqual(msg.content_type, "text")

    def test_webhook_post_reaction_message(self):
        """Test POST webhook with reaction message."""
        mock_request = self._make_mock_request("POST")
        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "metadata": {"phone_number_id": "webhook_ep_phone_id"},
                        "contacts": [{"profile": {"name": "Reactor"}}],
                        "messages": [{
                            "from": "919900112289",
                            "id": "wamid.webhook_ep_reaction_1",
                            "type": "reaction",
                            "reaction": {
                                "emoji": "\U0001f44d",
                                "message_id": "wamid.original_reacted_to"
                            },
                        }]
                    }
                }]
            }]
        }
        frappe.local.form_dict = frappe._dict(payload)

        with patch("frappe_whatsapp.utils.webhook.frappe.request", mock_request):
            from frappe_whatsapp.utils.webhook import webhook
            webhook()

        self.assertTrue(
            frappe.db.exists("WhatsApp Message", {"message_id": "wamid.webhook_ep_reaction_1"})
        )
        msg = frappe.get_doc("WhatsApp Message", {"message_id": "wamid.webhook_ep_reaction_1"})
        self.assertEqual(msg.content_type, "reaction")
        self.assertEqual(msg.message, "\U0001f44d")

    def test_webhook_post_button_message(self):
        """Test POST webhook with button reply message."""
        mock_request = self._make_mock_request("POST")
        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "metadata": {"phone_number_id": "webhook_ep_phone_id"},
                        "contacts": [{"profile": {"name": "Button User"}}],
                        "messages": [{
                            "from": "919900112290",
                            "id": "wamid.webhook_ep_button_1",
                            "type": "button",
                            "button": {"text": "Yes, confirm"},
                        }]
                    }
                }]
            }]
        }
        frappe.local.form_dict = frappe._dict(payload)

        with patch("frappe_whatsapp.utils.webhook.frappe.request", mock_request):
            from frappe_whatsapp.utils.webhook import webhook
            webhook()

        msg = frappe.get_doc("WhatsApp Message", {"message_id": "wamid.webhook_ep_button_1"})
        self.assertEqual(msg.content_type, "button")
        self.assertEqual(msg.message, "Yes, confirm")

    def test_webhook_post_reply_message(self):
        """Test POST webhook with reply context."""
        mock_request = self._make_mock_request("POST")
        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "metadata": {"phone_number_id": "webhook_ep_phone_id"},
                        "contacts": [{"profile": {"name": "Reply User"}}],
                        "messages": [{
                            "from": "919900112291",
                            "id": "wamid.webhook_ep_reply_1",
                            "type": "text",
                            "text": {"body": "This is a reply"},
                            "context": {"id": "wamid.original_msg_replied_to"},
                        }]
                    }
                }]
            }]
        }
        frappe.local.form_dict = frappe._dict(payload)

        with patch("frappe_whatsapp.utils.webhook.frappe.request", mock_request):
            from frappe_whatsapp.utils.webhook import webhook
            webhook()

        msg = frappe.get_doc("WhatsApp Message", {"message_id": "wamid.webhook_ep_reply_1"})
        self.assertEqual(msg.is_reply, 1)
        self.assertEqual(msg.reply_to_message_id, "wamid.original_msg_replied_to")

    def test_webhook_post_status_update(self):
        """Test POST webhook with status update (no messages)."""
        # Create a message to update
        msg = frappe.get_doc({
            "doctype": "WhatsApp Message",
            "type": "Outgoing",
            "to": "919900112292",
            "message": "Status update test",
            "message_id": "wamid.webhook_ep_status_1",
            "content_type": "text",
            "whatsapp_account": "Test WA Webhook EP Account",
        })
        msg.flags.ignore_validate = True
        msg.db_insert()
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture must be visible to later queries

        mock_request = self._make_mock_request("POST")
        payload = {
            "entry": [{
                "changes": [{
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "webhook_ep_phone_id"},
                        "statuses": [{
                            "id": "wamid.webhook_ep_status_1",
                            "status": "read",
                            "conversation": {"id": "conv_456"}
                        }]
                    }
                }]
            }]
        }
        frappe.local.form_dict = frappe._dict(payload)

        with patch("frappe_whatsapp.utils.webhook.frappe.request", mock_request):
            from frappe_whatsapp.utils.webhook import webhook
            webhook()

        msg.reload()
        self.assertEqual(msg.status, "read")
        self.assertEqual(msg.conversation_id, "conv_456")

    def test_webhook_post_template_status_update(self):
        """Template status updates have no metadata.phone_number_id; the handler
        must still route them to update_status. Regression for PR #231."""
        template_id = "webhook_ep_tmpl_id_status"
        template_name = "ep_status_template-en"

        # Seed the template (or reset its status if a prior run left it APPROVED)
        if frappe.db.exists("WhatsApp Templates", template_name):
            frappe.db.set_value(
                "WhatsApp Templates", template_name, "status", "PENDING"
            )
        else:
            doc = frappe.get_doc({
                "doctype": "WhatsApp Templates",
                "template_name": "ep_status_template",
                "actual_name": "ep_status_template",
                "template": "Status webhook regression template",
                "category": "TRANSACTIONAL",
                "language": frappe.db.get_value("Language", {"language_code": "en"}) or "en",
                "language_code": "en",
                "whatsapp_account": "Test WA Webhook EP Account",
                "status": "PENDING",
                "id": template_id,
            })
            doc.db_insert()
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture must be visible to later queries

        mock_request = self._make_mock_request("POST")
        payload = {
            "entry": [{
                "changes": [{
                    "field": "message_template_status_update",
                    "value": {
                        "event": "APPROVED",
                        "message_template_id": template_id,
                        "message_template_name": "ep_status_template",
                        "message_template_language": "en_US",
                        "reason": None,
                    },
                }],
            }],
        }
        frappe.local.form_dict = frappe._dict(payload)

        with patch("frappe_whatsapp.utils.webhook.frappe.request", mock_request):
            from frappe_whatsapp.utils.webhook import webhook
            webhook()

        status = frappe.db.get_value(
            "WhatsApp Templates", {"id": template_id}, "status"
        )
        self.assertEqual(status, "APPROVED")

    def test_webhook_creates_notification_log(self):
        """Test that webhook POST creates a notification log entry."""
        mock_request = self._make_mock_request("POST")
        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "metadata": {"phone_number_id": "webhook_ep_phone_id"},
                        "contacts": [{"profile": {"name": "Log Test"}}],
                        "messages": [{
                            "from": "919900112293",
                            "id": "wamid.webhook_ep_log_1",
                            "type": "text",
                            "text": {"body": "Log test"},
                        }]
                    }
                }]
            }]
        }
        frappe.local.form_dict = frappe._dict(payload)

        with patch("frappe_whatsapp.utils.webhook.frappe.request", mock_request):
            from frappe_whatsapp.utils.webhook import webhook
            webhook()

        # Should have created a notification log
        logs = frappe.get_all("WhatsApp Notification Log", filters={"template": "Webhook"})
        self.assertTrue(len(logs) > 0)
