import io
import json
import urllib.error
import unittest
from unittest.mock import MagicMock, patch
import semantic


class LocalModelTests(unittest.TestCase):
    def test_refused_connection_starts_installed_service_then_retries(self):
        opener = MagicMock()
        opener.open.side_effect = [
            urllib.error.URLError(ConnectionRefusedError()),
            io.BytesIO(json.dumps({'embeddings': [[1., 0.]]}).encode()),
        ]
        with patch.object(semantic.urllib.request, 'build_opener', return_value=opener), patch.object(semantic, 'start_local_model') as start:
            self.assertEqual(semantic.embed(['text']), [[1., 0.]])
            start.assert_called_once()
            self.assertEqual(opener.open.call_count, 2)

    def test_model_http_error_does_not_restart_service(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError('local', 404, 'model missing', {}, None)
        with patch.object(semantic.urllib.request, 'build_opener', return_value=opener), patch.object(semantic, 'start_local_model') as start:
            with self.assertRaisesRegex(ValueError, 'векторизация недоступна'):
                semantic.embed(['text'])
            start.assert_not_called()

    def test_missing_installation_does_not_spawn_or_download(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.URLError(ConnectionRefusedError())
        with patch.object(semantic.urllib.request, 'build_opener', return_value=opener), patch.object(semantic.shutil, 'which', return_value=None), patch.object(semantic.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'не установлена'):
                semantic.start_local_model()
            spawn.assert_not_called()
