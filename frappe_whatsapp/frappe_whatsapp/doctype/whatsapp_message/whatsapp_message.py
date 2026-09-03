# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt
import json
import frappe
from frappe import _, throw
from frappe.model.document import Document

from frappe_whatsapp.providers import get_provider
from frappe_whatsapp.providers.errors import (
    UnsupportedFeatureError,
    extract_integration_error,
)
from frappe_whatsapp.providers.types import ButtonParam, FlowSpec, OutboundMessage, TemplateSpec
from frappe_whatsapp.utils import get_whatsapp_account, format_number

class WhatsAppMessage(Document):
    def validate(self):
        self.set_whatsapp_account()

    def on_update(self):
        self.update_profile_name()

    def update_profile_name(self):
        number = self.get("from")
        if not number:
            return
        from_number = format_number(number)

        if (
            self.has_value_changed("profile_name")
            and self.profile_name
            and from_number
            and frappe.db.exists("WhatsApp Profiles", {"number": from_number})
        ):
            profile_id = frappe.get_value("WhatsApp Profiles", {"number": from_number}, "name")
            frappe.db.set_value("WhatsApp Profiles", profile_id, "profile_name", self.profile_name)

    def create_whatsapp_profile(self):
        number = format_number(self.get("from") or self.to)
        if not frappe.db.exists("WhatsApp Profiles", {"number": number}):
            frappe.get_doc({
                "doctype": "WhatsApp Profiles",
                "profile_name": self.profile_name,
                "number": number,
                "whatsapp_account": self.whatsapp_account
            }).insert(ignore_permissions=True)

    def set_whatsapp_account(self):
        """Set whatsapp account to default if missing"""
        if not self.whatsapp_account:
            account_type = 'outgoing' if self.type == 'Outgoing' else 'incoming'
            default_whatsapp_account = get_whatsapp_account(account_type=account_type)
            if not default_whatsapp_account:
                throw(_("Please set a default outgoing WhatsApp Account or Select available WhatsApp Account"))
            else:
                self.whatsapp_account = default_whatsapp_account.name

    """Send whats app messages."""
    def before_insert(self):
        """Send message."""
        self.set_whatsapp_account()
        # Route to template path when a template is selected,
        # since message_type is read_only and cannot be set from the UI.
        if self.template:
            self.message_type = "Template"
        self.send_outgoing()
        self.create_whatsapp_profile()

    def send_outgoing(self):
        """Dispatch an Outgoing message through the account's provider.

        Called from `before_insert` for first-time sends and from bulk
        retry for re-sending Failed messages. No-op for non-Outgoing docs.
        On non-template sends, raises and sets status to Failed on error;
        on template sends, `send_template` raises on error.
        """
        if self.type != "Outgoing":
            return

        if self.message_type != "Template":
            provider = get_provider(self.whatsapp_account)
            message = self._build_outbound_message(provider)
            try:
                result = provider.send_message(message)
                self.message_id = result.message_id
                self.status = "Success"
            except Exception as e:
                self.status = "Failed"
                frappe.throw(f"Failed to send message {str(e)}")
        elif not self.message_id:
            self.send_template()

    def _build_outbound_message(self, provider):
        """Project this doc onto the provider-neutral outbound structure.

        Everything that reads `self` lives here; providers only ever see
        resolved values. Media URL joining is reproduced exactly as it was
        before the provider split, double slash included, so the Meta payload
        is unchanged; correcting it is a separate, deliberate change.
        """
        if self.attach and not self.attach.startswith("http"):
            link = frappe.utils.get_url() + "/" + self.attach
        else:
            link = self.attach

        message = OutboundMessage(
            to=format_number(self.to),
            content_type=self.content_type,
            body=self.message,
            media_url=link,
            is_reply=bool(self.is_reply),
            reply_to_message_id=self.reply_to_message_id,
            source_doc=self.name,
        )

        if self.content_type == "interactive":
            message.buttons = (
                json.loads(self.buttons) if isinstance(self.buttons, str) else self.buttons
            )
        elif self.content_type == "flow":
            message.flow = self._build_flow_spec()

        if provider.wants_client_message_id:
            message.client_message_id = frappe.generate_hash(length=32)
            message.callback_data = self.whatsapp_account

        return message

    def _build_flow_spec(self):
        """Resolve the WhatsApp Flow referenced by this message."""
        if not self.flow:
            frappe.throw(_("WhatsApp Flow is required for flow content type"))

        flow_doc = frappe.get_doc("WhatsApp Flow", self.flow)

        if not flow_doc.flow_id:
            frappe.throw(_("Flow must be created on WhatsApp before sending"))

        # Determine flow mode - draft flows can be tested with mode: "draft"
        flow_mode = None
        if flow_doc.status != "Published":
            flow_mode = "draft"
            frappe.msgprint(
                _("Sending flow in draft mode (for testing only)"), indicator="orange"
            )

        # Get first screen if not specified
        flow_screen = self.flow_screen
        if not flow_screen and flow_doc.screens:
            flow_screen = flow_doc.screens[0].screen_id

        return FlowSpec(
            flow_id=flow_doc.flow_id,
            screen=flow_screen,
            cta=self.flow_cta or flow_doc.flow_cta or "Open",
            # Flow token is required by WhatsApp; generate one if absent.
            token=self.flow_token or frappe.generate_hash(length=16),
            mode=flow_mode,
        )

    def send_template(self):
        """Send template."""
        template = frappe.get_doc("WhatsApp Templates", self.template)
        provider = get_provider(self.whatsapp_account)

        spec = TemplateSpec(
            name=template.actual_name or template.template_name,
            language_code=template.language_code,
        )

        template_parameters = []
        if template.sample_values:
            field_names = template.field_names.split(",") if template.field_names else template.sample_values.split(",")

            if self.body_param is not None:
                template_parameters = list(json.loads(self.body_param).values())
            elif self.flags.custom_ref_doc:
                custom_values = self.flags.custom_ref_doc
                template_parameters = [
                    custom_values.get(field_name.strip()) for field_name in field_names
                ]
            else:
                ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
                template_parameters = [
                    ref_doc.get_formatted(field_name.strip()) for field_name in field_names
                ]

            spec.body_params = template_parameters
            self.template_parameters = json.dumps(template_parameters)

        if template.header_type:
            # Prefer a per-message attachment; otherwise fall back to the
            # template's stored sample media (this is the path campaigns use,
            # since they never set `attach`).
            media_source = self.attach or template.sample
            if media_source:
                spec.header_type = template.header_type
                if media_source.startswith("http"):
                    spec.header_media_url = f'{media_source}'
                else:
                    spec.header_media_url = f'{frappe.utils.get_url()}{media_source}'

                if template.header_type == 'DOCUMENT':
                    spec.header_filename = (
                        media_source.split("/")[-1].split("?")[0] or "document.pdf"
                    )

        # We check this before standard buttons because MPM is an interactive action
        has_mpm = False
        if self.product_catalog_json:
            try:
                spec.product_catalog = json.loads(self.product_catalog_json)
                has_mpm = True
            except Exception as e:
                frappe.log_error(f"Failed to parse Product Catalog JSON: {str(e)}", "WhatsApp MPM Error")

        if template.buttons:
            # Only buttons with *runtime* parameters go into components.
            # Static Call Phone and static Visit Website buttons are applied
            # by Meta from the approved template — sending them here yields
            # "sub_type must be one of {...}" errors since Meta no longer
            # accepts `phone_number`. See issue #188.
            for idx, btn in enumerate(template.buttons):
                # Shift index if MPM was added at index 0
                current_idx = str(idx + 1) if has_mpm else str(idx)

                if btn.button_type == "Quick Reply":
                    spec.buttons.append(
                        ButtonParam(
                            index=current_idx,
                            sub_type="quick_reply",
                            value=btn.button_label,
                        )
                    )
                elif btn.button_type == "Visit Website" and btn.url_type == "Dynamic":
                    ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
                    spec.buttons.append(
                        ButtonParam(
                            index=current_idx,
                            sub_type="url",
                            value=ref_doc.get_formatted(btn.website_url),
                        )
                    )

        message = self._build_outbound_message(provider)
        message.template = spec
        result = provider.send_template(message)
        self.message_id = result.message_id

    def notify(self, data):
        """Send a pre-built Meta payload.

        Deprecated: kept because this is a public method on a doctype in an app
        with Server Scripts enabled, so removing it would silently break
        downstream callers. New code should go through the provider.
        """
        provider = get_provider(self.whatsapp_account)
        if not hasattr(provider, "send_raw"):
            raise UnsupportedFeatureError("raw payload send", provider.name)
        self.message_id = provider.send_raw(data).message_id

    def format_number(self, number):
        """Format number."""
        if number.startswith("+"):
            number = number[1 : len(number)]

        return number

    @frappe.whitelist()
    def send_read_receipt(self):
        provider = get_provider(self.whatsapp_account)
        try:
            if provider.mark_read(self.message_id):
                self.status = "marked as read"
                self.save()
                return True

        except Exception as e:
            error_message, _title = extract_integration_error(e)
            frappe.log_error("WhatsApp API Error", error_message)


def on_doctype_update():
    frappe.db.add_index("WhatsApp Message", ["reference_doctype", "reference_name"])


@frappe.whitelist()
def send_template(to, reference_doctype, reference_name, template):
    try:
        doc = frappe.get_doc({
            "doctype": "WhatsApp Message",
            "to": to,
            "type": "Outgoing",
            "message_type": "Template",
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "content_type": "text",
            "template": template
        })

        doc.save()
    except Exception as e:
        raise e
