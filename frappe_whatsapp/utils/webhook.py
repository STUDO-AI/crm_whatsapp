"""Webhook."""
import frappe
import json
import requests
import time
from frappe import _
from urllib.parse import urlparse
from werkzeug.wrappers import Response
import frappe.utils

from frappe_whatsapp.utils import get_whatsapp_account


INFOBIP_STATUS_MAP = {
	"PENDING": "sent",
	"ACCEPTED": "sent",
	"SENT": "sent",
	"DELIVERED": "delivered",
	"READ": "read",
	"SEEN": "read",
	"REJECTED": "failed",
	"UNDELIVERABLE": "failed",
	"EXPIRED": "failed",
	"FAILED": "failed",
}


@frappe.whitelist(allow_guest=True)
def webhook():
	"""Meta webhook."""
	if frappe.request.method == "GET":
		return get()
	return post()


def get():
	"""Get."""
	hub_challenge = frappe.form_dict.get("hub.challenge")
	verify_token = frappe.form_dict.get("hub.verify_token")
	webhook_verify_token = frappe.db.get_value(
		'WhatsApp Account',
		{"webhook_verify_token": verify_token},
		'webhook_verify_token'
	)
	if not webhook_verify_token:
		frappe.throw("No matching WhatsApp account")

	if frappe.form_dict.get("hub.verify_token") != webhook_verify_token:
		frappe.throw("Verify token does not match")

	return Response(hub_challenge, status=200)

def post():
	"""Post."""
	data = frappe.local.form_dict
	frappe.get_doc({
		"doctype": "WhatsApp Notification Log",
		"template": "Webhook",
		"meta_data": json.dumps(data)
	}).insert(ignore_permissions=True)

	messages = []
	phone_id = None
	try:
		messages = data["entry"][0]["changes"][0]["value"].get("messages", [])
		phone_id = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("metadata", {}).get("phone_number_id")
	except KeyError:
		messages = data["entry"]["changes"][0]["value"].get("messages", [])
	sender_profile_name = next(
		(
			contact.get("profile", {}).get("name")
			for entry in data.get("entry", [])
			for change in entry.get("changes", [])
			for contact in change.get("value", {}).get("contacts", [])
		),
		None,
	)

	whatsapp_account = get_whatsapp_account(phone_id) if phone_id else None

	# Only `messages` events carry `metadata.phone_number_id`. Status-change
	# events (`message_template_status_update`, message status callbacks) have
	# no metadata, so `phone_id` is None and `whatsapp_account` is also None
	# for them by design. Gating the entire handler on `whatsapp_account`
	# silently drops every template-status update; gate only the message-
	# ingestion branch instead.
	if messages and not whatsapp_account:
		return

	if messages:
		for message in messages:
			message_type = message['type']
			is_reply = True if message.get('context') and 'forwarded' not in message.get('context') else False
			reply_to_message_id = message['context']['id'] if is_reply else None
			if message_type == 'text':
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['text']['body'],
					"message_id": message['id'],
					"reply_to_message_id": reply_to_message_id,
					"is_reply": is_reply,
					"content_type":message_type,
					"profile_name":sender_profile_name,
					"whatsapp_account":whatsapp_account.name
				}).insert(ignore_permissions=True)
			elif message_type == 'reaction':
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['reaction']['emoji'],
					"reply_to_message_id": message['reaction']['message_id'],
					"message_id": message['id'],
					"content_type": "reaction",
					"profile_name":sender_profile_name,
					"whatsapp_account":whatsapp_account.name
				}).insert(ignore_permissions=True)
			elif message_type == 'interactive':
				interactive_data = message['interactive']
				interactive_type = interactive_data.get('type')

				# Handle button reply
				if interactive_type == 'button_reply':
					frappe.get_doc({
						"doctype": "WhatsApp Message",
						"type": "Incoming",
						"from": message['from'],
						"message": interactive_data['button_reply']['id'],
						"message_id": message['id'],
						"reply_to_message_id": reply_to_message_id,
						"is_reply": is_reply,
						"content_type": "button",
						"profile_name": sender_profile_name,
						"whatsapp_account": whatsapp_account.name
					}).insert(ignore_permissions=True)
				# Handle list reply
				elif interactive_type == 'list_reply':
					frappe.get_doc({
						"doctype": "WhatsApp Message",
						"type": "Incoming",
						"from": message['from'],
						"message": interactive_data['list_reply']['id'],
						"message_id": message['id'],
						"reply_to_message_id": reply_to_message_id,
						"is_reply": is_reply,
						"content_type": "button",
						"profile_name": sender_profile_name,
						"whatsapp_account": whatsapp_account.name
					}).insert(ignore_permissions=True)
				# Handle WhatsApp Flows (nfm_reply)
				elif interactive_type == 'nfm_reply':
					nfm_reply = interactive_data['nfm_reply']
					response_json_str = nfm_reply.get('response_json', '{}')

					# Parse the response JSON
					try:
						flow_response = json.loads(response_json_str)
					except json.JSONDecodeError:
						flow_response = {}

					# Create a summary message from the flow response
					summary_parts = []
					for key, value in flow_response.items():
						if value:
							summary_parts.append(f"{key}: {value}")
					summary_message = ", ".join(summary_parts) if summary_parts else "Flow completed"

					msg_doc = frappe.get_doc({
						"doctype": "WhatsApp Message",
						"type": "Incoming",
						"from": message['from'],
						"message": summary_message,
						"message_id": message['id'],
						"reply_to_message_id": reply_to_message_id,
						"is_reply": is_reply,
						"content_type": "flow",
						"flow_response": json.dumps(flow_response),
						"profile_name": sender_profile_name,
						"whatsapp_account": whatsapp_account.name
					}).insert(ignore_permissions=True)

					# Publish realtime event for flow response
					frappe.publish_realtime(  # nosemgrep: frappe-realtime-pick-room -- intentional site-wide fan-out for chat UIs (whatsapp_chat companion app) listening for inbound flow responses
						"whatsapp_flow_response",
						{
							"phone": message['from'],
							"message_id": message['id'],
							"flow_response": flow_response,
							"whatsapp_account": whatsapp_account.name
						}
					)
			# NEW: Handle Shopping Cart / Orders from MPM
			elif message_type == 'order':
				order_data = message['order']

				# Inject the raw data into product_catalog_json
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": _("New Order Received via WhatsApp"),
					"message_id": message['id'],
					"content_type": "order",
					"profile_name": sender_profile_name,
					"whatsapp_account": whatsapp_account.name,
					"product_catalog_json": json.dumps(order_data)
				}).insert(ignore_permissions=True)
			elif message_type in ["image", "audio", "video", "document"]:
				token = whatsapp_account.get_password("token")
				url = f"{whatsapp_account.url}/{whatsapp_account.version}/"

				media_id = message[message_type]["id"]
				headers = {
					'Authorization': 'Bearer ' + token

				}
				response = requests.get(f'{url}{media_id}/', headers=headers)

				if response.status_code == 200:
					media_data = response.json()
					media_url = media_data.get("url")
					mime_type = media_data.get("mime_type")
					file_extension = mime_type.split('/')[1]

					media_response = requests.get(media_url, headers=headers)
					if media_response.status_code == 200:

						file_data = media_response.content
						file_name = f"{frappe.generate_hash(length=10)}.{file_extension}"

						message_doc = frappe.get_doc({
							"doctype": "WhatsApp Message",
							"type": "Incoming",
							"from": message['from'],
							"message_id": message['id'],
							"reply_to_message_id": reply_to_message_id,
							"is_reply": is_reply,
							"message": message[message_type].get("caption", ""),
							"content_type" : message_type,
							"profile_name":sender_profile_name,
							"whatsapp_account":whatsapp_account.name
						}).insert(ignore_permissions=True)

						file = frappe.get_doc(
							{
								"doctype": "File",
								"file_name": file_name,
								"attached_to_doctype": "WhatsApp Message",
								"attached_to_name": message_doc.name,
								"content": file_data,
								"attached_to_field": "attach"
							}
						).save(ignore_permissions=True)


						message_doc.attach = file.file_url
						message_doc.save()
			elif message_type == "button":
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['button']['text'],
					"message_id": message['id'],
					"reply_to_message_id": reply_to_message_id,
					"is_reply": is_reply,
					"content_type": message_type,
					"profile_name":sender_profile_name,
					"whatsapp_account":whatsapp_account.name
				}).insert(ignore_permissions=True)
			else:
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message_id": message['id'],
					"message": message[message_type].get(message_type),
					"content_type" : message_type,
					"profile_name":sender_profile_name,
					"whatsapp_account":whatsapp_account.name
				}).insert(ignore_permissions=True)

	else:
		changes = None
		try:
			changes = data["entry"][0]["changes"][0]
		except KeyError:
			changes = data["entry"]["changes"][0]
		update_status(changes)
	return


@frappe.whitelist(allow_guest=True)
def infobip():
	"""Infobip inbound messages and delivery reports."""
	account = _get_infobip_account_from_key()
	data = _get_request_json()
	_log_webhook_payload("Infobip Webhook", data)

	for result in data.get("results") or []:
		try:
			if result.get("message"):
				_insert_infobip_message(result, account)
			else:
				_update_infobip_message_status(result)
		except Exception:
			_log_webhook_payload(
				"Infobip Webhook Error",
				{"result": result, "traceback": frappe.get_traceback()},
			)

	return {"success": True}


def _get_request_json() -> dict:
	data = getattr(frappe.request, "json", None)
	if isinstance(data, dict):
		return data
	if isinstance(frappe.local.form_dict, dict):
		return frappe.local.form_dict
	return {}


def _get_infobip_account_from_key():
	key = _get_infobip_webhook_key()
	if not key:
		frappe.throw(_("Webhook key is required."))

	account_name = frappe.db.get_value(
		"WhatsApp Account",
		{"webhook_verify_token": key},
		"name",
	)
	if not account_name:
		frappe.throw(_("No matching WhatsApp account"))

	account = frappe.get_doc("WhatsApp Account", account_name)
	if account.meta.has_field("provider") and account.get("provider") != "Infobip":
		frappe.throw(_("Webhook key does not belong to an Infobip account."))
	return account


def _get_infobip_webhook_key() -> str | None:
	key = frappe.form_dict.get("key")
	if key:
		return key

	args = getattr(frappe.request, "args", None)
	if args:
		return args.get("key")
	return None


def _get_infobip_account_for_result(result: dict, fallback_account):
	sender = _digits(result.get("to"))
	if sender:
		account_name = frappe.db.get_value("WhatsApp Account", {"phone_id": sender}, "name")
		if account_name:
			return frappe.get_doc("WhatsApp Account", account_name)
	return fallback_account


def _insert_infobip_message(result: dict, fallback_account) -> None:
	message = result.get("message") or {}
	message_id = result.get("messageId") or result.get("pairedMessageId")
	if message_id and frappe.db.exists("WhatsApp Message", {"message_id": message_id}):
		return

	account = _get_infobip_account_for_result(result, fallback_account)
	message_type = (message.get("type") or "").upper()
	content_type = _infobip_content_type(message_type)

	doc = frappe.get_doc(
		{
			"doctype": "WhatsApp Message",
			"type": "Incoming",
			"from": result.get("from"),
			"message": _infobip_message_text(message_type, message),
			"message_id": message_id,
			"reply_to_message_id": result.get("pairedMessageId"),
			"is_reply": bool(result.get("pairedMessageId")),
			"content_type": content_type,
			"profile_name": _infobip_profile_name(result),
			"whatsapp_account": account.name,
		}
	).insert(ignore_permissions=True)

	if content_type in ("image", "document", "audio", "video") and message.get("url"):
		_attach_infobip_media(doc, account, message)


def _infobip_content_type(message_type: str) -> str:
	if message_type == "TEXT":
		return "text"
	if message_type in ("BUTTON", "INTERACTIVE_BUTTON_REPLY", "INTERACTIVE_LIST_REPLY"):
		return "button"
	if message_type in ("IMAGE", "DOCUMENT", "AUDIO", "VIDEO"):
		return message_type.lower()
	if message_type == "STICKER":
		return "image"
	return "text"


def _infobip_message_text(message_type: str, message: dict) -> str:
	if message_type == "TEXT":
		return message.get("text") or ""
	if message_type in ("BUTTON", "INTERACTIVE_BUTTON_REPLY", "INTERACTIVE_LIST_REPLY"):
		return message.get("id") or message.get("title") or message.get("text") or ""
	if message_type in ("IMAGE", "DOCUMENT", "AUDIO", "VIDEO", "STICKER"):
		return message.get("caption") or message.get("url") or ""
	return message.get("text") or message.get("caption") or f"Unsupported Infobip message: {message_type}"


def _infobip_profile_name(result: dict) -> str | None:
	contact = result.get("contact") or {}
	name = contact.get("name") or {}
	if isinstance(name, dict):
		return name.get("formatted_name")
	return contact.get("profileName") or contact.get("profile_name")


def _attach_infobip_media(message_doc, account, message: dict) -> None:
	media_url = message["url"]
	if not _is_allowed_infobip_media_url(media_url, account):
		frappe.log_error(f"Blocked Infobip media URL: {media_url}", "Infobip Webhook Media")
		return

	headers = {"Authorization": f"App {account.get_password('token')}"}
	response = requests.get(media_url, headers=headers, timeout=30)
	if response.status_code != 200:
		frappe.log_error(
			f"Could not download Infobip media: {response.status_code}",
			"Infobip Webhook Media",
		)
		return

	content_type = response.headers.get("Content-Type") or "application/octet-stream"
	file_name = _infobip_media_filename(response.headers, content_type)
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": "WhatsApp Message",
			"attached_to_name": message_doc.name,
			"attached_to_field": "attach",
			"content": response.content,
		}
	).save(ignore_permissions=True)

	message_doc.attach = file_doc.file_url
	message_doc.save(ignore_permissions=True)


def _is_allowed_infobip_media_url(media_url: str, account) -> bool:
	parsed = urlparse(media_url)
	if parsed.scheme != "https" or not parsed.hostname:
		return False

	allowed_hosts = {"api.infobip.com"}
	account_host = urlparse((account.get("url") or "").rstrip("/")).hostname
	if account_host:
		allowed_hosts.add(account_host)

	host = parsed.hostname.lower()
	return host in allowed_hosts or host.endswith(".api.infobip.com")


def _infobip_media_filename(headers, content_type: str) -> str:
	content_disposition = headers.get("Content-Disposition")
	if content_disposition and "filename=" in content_disposition:
		return content_disposition.split("filename=", 1)[1].strip().strip('"')
	return f"{frappe.generate_hash(length=10)}{_extension_from_content_type(content_type)}"


def _extension_from_content_type(content_type: str) -> str:
	content_type = (content_type or "").split(";", 1)[0].strip().lower()
	extensions = {
		"image/jpeg": ".jpg",
		"image/jpg": ".jpg",
		"image/png": ".png",
		"image/webp": ".webp",
		"application/pdf": ".pdf",
		"audio/ogg": ".ogg",
		"application/ogg": ".ogg",
		"audio/mpeg": ".mp3",
		"audio/mp3": ".mp3",
		"audio/mp4": ".m4a",
		"audio/aac": ".m4a",
		"video/mp4": ".mp4",
	}
	return extensions.get(content_type, ".bin")


def _update_infobip_message_status(result: dict) -> None:
	message_id = result.get("messageId")
	if not message_id:
		return

	name = frappe.db.get_value("WhatsApp Message", filters={"message_id": message_id})
	if not name:
		return

	status = _normalize_infobip_status(result.get("status"))
	if not status:
		return

	doc = frappe.get_doc("WhatsApp Message", name)
	doc.status = status
	doc.save(ignore_permissions=True)


def _normalize_infobip_status(status) -> str | None:
	if isinstance(status, dict):
		status = status.get("groupName") or status.get("name")
	if not status:
		return None
	return INFOBIP_STATUS_MAP.get(str(status).upper(), str(status).lower())


def _log_webhook_payload(template: str, payload: dict) -> None:
	try:
		frappe.get_doc(
			{
				"doctype": "WhatsApp Notification Log",
				"template": template,
				"meta_data": json.dumps(payload),
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Infobip Webhook: could not write log")


def _digits(value) -> str:
	return "".join(ch for ch in str(value or "") if ch.isdigit())


def update_status(data):
	"""Update status hook."""
	if data.get("field") == "message_template_status_update":
		update_template_status(data['value'])

	elif data.get("field") == "messages":
		update_message_status(data['value'])

def update_template_status(data):
	"""Update template status."""
	frappe.db.sql(
		"""UPDATE `tabWhatsApp Templates`
		SET status = %(event)s
		WHERE id = %(message_template_id)s""",
		data
	)

def update_message_status(data):
	"""Update message status."""
	id = data['statuses'][0]['id']
	status = data['statuses'][0]['status']
	conversation = data['statuses'][0].get('conversation', {}).get('id')
	name = frappe.db.get_value("WhatsApp Message", filters={"message_id": id})

	doc = frappe.get_doc("WhatsApp Message", name)
	doc.status = status
	if conversation:
		doc.conversation_id = conversation
	doc.save(ignore_permissions=True)
