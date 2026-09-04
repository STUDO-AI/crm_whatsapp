# Copyright (c) 2026, Studo and Contributors
# See license.txt
"""Tests for outbound media resolution: the signed guest proxy and audio remux."""

from urllib.parse import parse_qs, urlparse

import frappe
from frappe_whatsapp.testing import IntegrationTestCase

from frappe_whatsapp.providers.media import (
    _media_token,
    download_outbound_media,
    remux_webm_file_to_ogg,
    resolve_public_media_url,
)


class TestOutboundMediaProxy(IntegrationTestCase):
    def _make_private_file(self, name: str, content: bytes = b"hello-media") -> "frappe.Document":
        file_doc = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": name,
                "is_private": 1,
                "content": content,
            }
        ).insert(ignore_permissions=True)
        self.addCleanup(lambda: frappe.delete_doc("File", file_doc.name, force=True))
        return file_doc

    def test_absolute_url_passes_through(self):
        url = "https://cdn.example.com/a.jpg"
        self.assertEqual(resolve_public_media_url(url), url)

    def test_private_file_becomes_signed_proxy_url(self):
        file_doc = self._make_private_file("media_proxy_test.png")
        url = resolve_public_media_url(file_doc.file_url)

        parsed = urlparse(url)
        self.assertIn("download_outbound_media", parsed.path)
        qs = parse_qs(parsed.query)
        self.assertEqual(qs["file"][0], file_doc.name)
        # The token must verify for the advertised (file, expires) pair.
        expected = _media_token(qs["file"][0], int(qs["expires"][0]))
        self.assertEqual(qs["token"][0], expected)

    def test_proxy_rejects_forged_token(self):
        file_doc = self._make_private_file("media_forged_test.png")
        qs = parse_qs(urlparse(resolve_public_media_url(file_doc.file_url)).query)
        with self.assertRaises(frappe.PermissionError):
            download_outbound_media(file=qs["file"][0], expires=qs["expires"][0], token="deadbeef")

    def test_proxy_streams_content_with_valid_token(self):
        file_doc = self._make_private_file("media_stream_test.png", b"the-bytes")
        qs = parse_qs(urlparse(resolve_public_media_url(file_doc.file_url)).query)

        response = download_outbound_media(
            file=qs["file"][0], expires=qs["expires"][0], token=qs["token"][0]
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(), b"the-bytes")

    def test_remux_ignores_non_webm(self):
        # Only `.webm` audio is remuxed; anything else is left to the caller.
        self.assertIsNone(remux_webm_file_to_ogg("/private/files/note.ogg"))
        self.assertIsNone(remux_webm_file_to_ogg(None))
