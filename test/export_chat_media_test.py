"""Synthetic regressions for HTML emoji identity and resource isolation."""
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
import os
import struct
import hashlib
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'export_chat_html.py'
spec = importlib.util.spec_from_file_location('export_chat_html', SCRIPT)
export = importlib.util.module_from_spec(spec)
# These tests exercise rendering and resource selection, not SQLCipher access.
with patch.dict(sys.modules, {'sqlcipher3': types.SimpleNamespace(dbapi2=sqlite3)}):
    spec.loader.exec_module(export)

MEDIA_MD5 = 'a' * 32
IMAGE = ('R0lGODlh', 'image/gif')


def message(server_id=101, content=''):
    return (7, server_id, 47, 0, 1, 1700000000, 0, '', content, b'')


class EmojiExportTests(unittest.TestCase):
    def render(self, row, resources=None):
        return export.format_message(
            row, 'example-contact', '', {f'md5:{MEDIA_MD5}': IMAGE},
            resource_map=resources,
        )

    def test_cached_emoji_keeps_emoji_label(self):
        result = self.render(message(content=f'<emoji md5="{MEDIA_MD5}"/>'))
        self.assertEqual(result['image_b64'], IMAGE[0])
        self.assertIn('[\u8868\u60c5]', result['display'])

    def test_exact_server_resource_matches(self):
        result = self.render(message(), {'server:101': [MEDIA_MD5]})
        self.assertEqual(result['image_b64'], IMAGE[0])

    def test_colliding_local_id_does_not_match(self):
        result = self.render(message(), {'local:7': [MEDIA_MD5]})
        self.assertIsNone(result['image_b64'])

    def test_zero_server_id_does_not_match(self):
        result = self.render(message(0), {'server:0': [MEDIA_MD5]})
        self.assertIsNone(result['image_b64'])

    def test_resource_loading_excludes_unrelated_and_zero_server_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'db_storage/message/message_resource.db'
            database.parent.mkdir(parents=True)
            connection = sqlite3.connect(database)
            connection.execute('CREATE TABLE MessageResourceInfo '
                               '(message_svr_id, message_local_id, packed_info)')
            connection.executemany('INSERT INTO MessageResourceInfo VALUES (?, ?, ?)', [
                (101, 7, MEDIA_MD5), (202, 7, 'b' * 32), (0, 7, 'c' * 32),
            ])
            connection.commit()
            with patch.object(export, 'connect', return_value=(connection, connection.cursor())):
                result = export.load_resource_media_map(
                    directory, '', '', [message(), message(0)],
                    {f'md5:{MEDIA_MD5}': IMAGE},
                )
            self.assertEqual(result, {'server:101': [MEDIA_MD5]})

    def test_v2_media_is_derived_and_decoded_from_local_kvcomm(self):
        from Crypto.Cipher import AES
        from Crypto.Util import Padding

        with tempfile.TemporaryDirectory() as directory:
            account = Path(directory) / 'wxid_example_ab12'
            media_dir = account / 'msg/attach/chat/2026-09/Img'
            kvcomm = Path(directory) / 'kvcomm'
            media_dir.mkdir(parents=True)
            kvcomm.mkdir()
            code = 513
            (kvcomm / f'key_{code}_test.statistic').write_bytes(b'')
            media_md5 = '1' * 32
            plaintext = b'\x89PNG\r\n\x1a\n' + b'synthetic-image-payload'
            aes_key = hashlib.md5(f'{code}wxid_example'.encode()).hexdigest()[:16].encode('ascii')
            encrypted = AES.new(aes_key, AES.MODE_ECB).encrypt(Padding.pad(plaintext, 16))
            container = struct.pack('<6sLLx', export.V2_MAGIC, len(plaintext), 0) + encrypted
            path = media_dir / f'{media_md5}_t.dat'
            path.write_bytes(container)

            key = export.resolve_v2_media_key(account, 'wxid_example_ab12', kvcomm)
            self.assertEqual(key, (code & 0xff, aes_key))
            decoded = export.decode_wechat_media(container[:64], path, key)
            self.assertEqual(decoded, (plaintext, 'image/png'))

    def test_app_card_with_cached_cover_keeps_cdata_link(self):
        content = (
            '<appmsg><title><![CDATA[Bilibili example]]></title>'
            '<des><![CDATA[Video description]]></des><type>5</type>'
            '<url><![CDATA[https://www.bilibili.com/video/BV-example]]></url>'
            f'<md5>{MEDIA_MD5}</md5></appmsg>'
        )
        row = (7, 101, 49, 0, 1, 1700000000, 0, '', content, b'')
        result = self.render(row, {'server:101': [MEDIA_MD5]})
        self.assertIn('class="msg-app"', result['display'])
        self.assertIn('href="https://www.bilibili.com/video/BV-example"', result['display'])
        self.assertNotIn('[\u56fe\u7247]', result['display'])

    def test_bilibili_share_url_is_not_used_as_cover_image(self):
        content = (
            '<appmsg><title>Video</title><url><![CDATA['
            'https://b23.tv/Br96Gss?share_source=weixin'
            ']]></url></appmsg>'
        )
        self.assertIsNone(export.extract_appmsg_image(content))

    def test_emoticon_cbc_uses_key_as_iv(self):
        from Crypto.Cipher import AES
        from Crypto.Util import Padding

        key = bytes.fromhex('12' * 16)
        plaintext = b'GIF89a' + b'synthetic-emoticon'
        encrypted = AES.new(key, AES.MODE_CBC, key).encrypt(Padding.pad(plaintext, 16))
        self.assertEqual(export._decrypt_aes_cbc(encrypted, key.hex()), plaintext)

    def test_compressed_source_xml_supplies_type_one_emoji(self):
        import zstandard
        content = (
            '<emoji md5="' + MEDIA_MD5 + '" aeskey="' + ('12' * 16) + '" '
            'cdnurl="https://example.test/emoji" />'
        )
        source = zstandard.ZstdCompressor().compress(content.encode())
        self.assertIn('<emoji', export.decode_message_content(source))
        row = (7, 101, 1, 0, 1, 1700000000, 0, source, '[\u6253\u8138]', b'')
        self.assertIsNotNone(export.get_cached_image({f'md5:{MEDIA_MD5}': IMAGE}, 7, 1700000000, content, [MEDIA_MD5]))
        result = export.format_message(row, 'example-contact', '', {f'md5:{MEDIA_MD5}': IMAGE}, resource_map={'server:101': [MEDIA_MD5]})
        self.assertIn('[\u6253\u8138]', result['display'])
        self.assertIn('data:image/gif', result['display'])

    def test_message_content_emoji_xml_wins_over_pua_only_source(self):
        import zstandard
        content = (
            '<msg><emoji fromusername="wxid_sender" '
            'tousername="wxid_receiver" type="2" '
            'md5="' + MEDIA_MD5 + '" '
            'encrypturl="https://example.test/encrypted" '
            'aeskey="' + ('12' * 16) + '" /></msg>'
        )
        source = zstandard.ZstdCompressor().compress(
            b'<msgsource><pua>1</pua><signature>only-signature</signature></msgsource>'
        )
        row = (7, 101, 1, 0, 1, 1700000000, 0, source, content, b'')
        result = export.format_message(
            row, 'example-contact', '', {f'md5:{MEDIA_MD5}': IMAGE},
            resource_map={'server:101': [MEDIA_MD5]},
        )
        self.assertIn('data:image/gif', result['display'])

    def test_entity_escaped_emoji_xml_is_decoded(self):
        content = (
            '&lt;msg&gt;&lt;emoji md5=&quot;' + MEDIA_MD5 + '&quot; '
            'encrypturl=&quot;https://example.test/encrypted&quot; '
            'aeskey=&quot;' + ('12' * 16) + '&quot; /&gt;&lt;/msg&gt;'
        )
        row = (7, 101, 1, 0, 1, 1700000000, 0, b'', content, b'')
        result = export.format_message(
            row, 'example-contact', '', {f'md5:{MEDIA_MD5}': IMAGE},
            resource_map={'server:101': [MEDIA_MD5]},
        )
        self.assertIn('data:image/gif', result['display'])

    def test_signature_only_facepalm_uses_bundled_default_emoji(self):
        source = b'<msgsource><pua>1</pua><signature>N0_V1_Sy7NKsXR|v1_kQSlFQy4</signature></msgsource>'
        row = (7, 101, 1, 0, 1, 1700000000, 0, source, '[\u6253\u8138]', b'')
        result = export.format_message(row, 'example-contact', '', {}, resource_map={})
        self.assertIn('[\u6253\u8138]', result['display'])
        self.assertIn('data:image/png;base64,', result['display'])

    def test_signature_only_concerned_uses_bundled_default_emoji(self):
        source = b'<msgsource><pua>1</pua><signature>only-signature</signature></msgsource>'
        row = (7, 102, 1, 0, 1, 1700000000, 0, source, '[\u76b1\u7709]', b'')
        result = export.format_message(row, 'example-contact', '', {}, resource_map={})
        self.assertIn('[\u76b1\u7709]', result['display'])
        self.assertIn('data:image/png;base64,', result['display'])

    def test_signature_only_respect_uses_bundled_default_emoji(self):
        source = b'<msgsource><pua>1</pua><signature>only-signature</signature></msgsource>'
        row = (7, 103, 1, 0, 1, 1700000000, 0, source, '[\u5408\u5341]', b'')
        result = export.format_message(row, 'example-contact', '', {}, resource_map={})
        self.assertIn('[\u5408\u5341]', result['display'])
        self.assertIn('data:image/png;base64,', result['display'])

    def test_forwarded_emoji_xml_does_not_dump_raw_xml(self):
        content = (
            '<?xml version="1.0"?><msg><appmsg><title>看到这个表情就想笑</title>'
            '<refermsg><type>47</type><content><msg><emoji md5="abc" />'
            '</msg></content></refermsg></appmsg></msg>'
        )
        row = (7, 104, 1, 0, 1, 1700000000, 0, b'', content, b'')
        result = export.format_message(row, 'example-contact', '', {}, resource_map={})
        self.assertIn('看到这个表情就想笑', result['display'])
        self.assertNotIn('&lt;?xml', result['display'])

    def test_forwarded_emoji_restores_wechat_url_separator(self):
        content = '<msg><emoji encrypturl="http*#*//example.test/emoji.png" /></msg>'
        self.assertEqual(
            export.extract_appmsg_image(content),
            'http://example.test/emoji.png',
        )


if __name__ == '__main__':
    unittest.main()
