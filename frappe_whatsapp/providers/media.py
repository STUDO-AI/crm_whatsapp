# Copyright (c) 2026, Studo and Contributors
# For license information, please see license.txt
"""Turn a `WhatsApp Message.attach` value into a URL the provider can fetch.

Both Meta and Infobip fetch outbound media over plain HTTPS with **no
credentials of ours**, so whatever we hand them has to be readable by an
anonymous client on the public internet. Frappe's `/private/files/...?key=`
share links are not reliably guest-readable (the provider gets a 403, which
surfaces as Infobip "Media hosting error 7013"), so private files are served
through a dedicated, signed, guest-readable proxy endpoint instead.
"""

import hashlib
import hmac
import mimetypes
import os
import subprocess
import tempfile
from urllib.parse import quote

import frappe

# How long a signed media URL stays valid. Providers fetch outbound media within
# seconds of the send; this is generous slack for retries and provider caching.
_MEDIA_URL_TTL_SECONDS = 3 * 24 * 60 * 60

_PROXY_METHOD = "frappe_whatsapp.providers.media.download_outbound_media"


def resolve_public_media_url(attach: str | None, share_doc=None) -> str | None:
    """Absolute, anonymously fetchable URL for `attach`.

    Absolute URLs pass through untouched. A private Frappe file is turned into a
    signed proxy URL (`download_outbound_media`) that any client can GET without
    a session. A public Frappe file is just made absolute.
    """
    if not attach:
        return None

    if attach.startswith("http"):
        return attach

    if attach.startswith("/private/"):
        file_name = frappe.db.get_value("File", {"file_url": attach}, "name")
        if file_name:
            return _signed_media_url(file_name)
        # Could not locate the File row; fall through to a plain absolute URL.

    return f"{frappe.utils.get_url()}{attach}"


# --------------------------------------------------------------------- signing


def _signed_media_url(file_name: str) -> str:
    expires = int(frappe.utils.now_datetime().timestamp()) + _MEDIA_URL_TTL_SECONDS
    token = _media_token(file_name, expires)
    base = frappe.utils.get_url()
    return (
        f"{base}/api/method/{_PROXY_METHOD}"
        f"?file={quote(file_name)}&expires={expires}&token={token}"
    )


def _media_token(file_name: str, expires: int) -> str:
    secret = (frappe.local.conf.get("encryption_key") or "").encode()
    payload = f"{file_name}:{expires}".encode()
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


# -------------------------------------------------------------------- endpoint


@frappe.whitelist(allow_guest=True)
def download_outbound_media(file: str, expires: str, token: str):
    """Stream a WhatsApp media File to an anonymous provider, gated by an HMAC.

    The token is an HMAC (site `encryption_key`) over `file:expires`, so it
    cannot be forged and grants access to exactly one File until it expires.
    """
    from werkzeug.wrappers import Response

    try:
        expires_ts = int(expires)
    except (TypeError, ValueError):
        raise frappe.PermissionError(frappe._("Invalid media token"))

    expected = _media_token(file, expires_ts)
    if not hmac.compare_digest(str(token or ""), expected):
        raise frappe.PermissionError(frappe._("Invalid media token"))

    if frappe.utils.now_datetime().timestamp() > expires_ts:
        raise frappe.PermissionError(frappe._("Media link expired"))

    file_doc = frappe.get_doc("File", file)
    content = file_doc.get_content()
    content_type = (
        mimetypes.guess_type(file_doc.file_name or "")[0] or "application/octet-stream"
    )

    return Response(
        content,
        content_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{file_doc.file_name}"',
            "Content-Length": str(len(content)),
        },
    )


# ----------------------------------------------------------------- audio remux


def remux_webm_file_to_ogg(attach: str, share_doc=None) -> str | None:
    """Return the file_url of an OGG/OPUS copy of a `.webm` audio File.

    WhatsApp only accepts audio as OGG (OPUS codec), AAC, AMR, MP3 or M4A;
    browser recorders emit `audio/webm; codecs=opus`. The OPUS stream is already
    correct, so we remux the container (lossless stream copy), re-encoding only
    if a straight copy fails. Returns None (leaving the caller to send the
    original) if the File is missing or `ffmpeg` is unavailable.
    """
    if not attach or not attach.lower().endswith(".webm"):
        return None

    file_name = frappe.db.get_value("File", {"file_url": attach}, "name")
    if not file_name:
        return None

    source = frappe.get_doc("File", file_name)
    webm_bytes = source.get_content()
    ogg_bytes = _webm_to_ogg_bytes(webm_bytes)
    if not ogg_bytes:
        return None

    ogg_name = source.file_name[: -len(".webm")] + ".ogg"
    ogg_file = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": ogg_name,
            "is_private": 1,
            "content": ogg_bytes,
            "attached_to_doctype": source.attached_to_doctype,
            "attached_to_name": source.attached_to_name,
        }
    ).insert(ignore_permissions=True)
    return ogg_file.file_url


def _webm_to_ogg_bytes(content: bytes) -> bytes | None:
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as src_f:
        src_f.write(content)
        src_path = src_f.name
    dst_path = f"{src_path[:-len('.webm')]}.ogg"
    try:
        # Preferred: stream-copy the OPUS track into an OGG container (fast, lossless).
        # Fallback: re-encode to OPUS if the source track is not copy-compatible.
        for codec_args in (["-c:a", "copy"], ["-c:a", "libopus", "-b:a", "32k"]):
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", src_path, *codec_args, dst_path],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                with open(dst_path, "rb") as out:
                    data = out.read()
                if data:
                    return data
            except FileNotFoundError:
                frappe.log_error(
                    "ffmpeg not found on the backend image; cannot convert WhatsApp "
                    "voice notes from webm to ogg. Install ffmpeg in the image.",
                    "WhatsApp: audio remux",
                )
                return None
            except subprocess.CalledProcessError:
                continue  # try the next codec strategy
        frappe.log_error(frappe.get_traceback(), "WhatsApp: webm->ogg remux failed")
        return None
    finally:
        for path in (src_path, dst_path):
            try:
                os.remove(path)
            except OSError:
                pass
