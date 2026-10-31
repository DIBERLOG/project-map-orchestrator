import unittest
from unittest.mock import patch,MagicMock
import launch


class LauncherTests(unittest.TestCase):
    def test_ready_server_is_reused_without_process_creation(self):
        with patch.object(launch,'ready',return_value=True),patch.object(launch.subprocess,'Popen') as spawn:
            self.assertIsNone(launch.ensure_server())
            spawn.assert_not_called()

    def test_unrelated_port_is_never_stopped_or_replaced(self):
        socket=MagicMock();socket.__enter__.return_value.connect_ex.return_value=0
        with patch.object(launch,'ready',return_value=False),patch.object(launch.socket,'socket',return_value=socket),patch.object(launch.time,'sleep'),patch.object(launch.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError,'занят'):launch.ensure_server()
            spawn.assert_not_called()

    def test_simultaneous_start_waits_for_first_launcher(self):
        socket=MagicMock();socket.__enter__.return_value.connect_ex.return_value=0
        with patch.object(launch,'ready',side_effect=[False,False,True]),patch.object(launch.socket,'socket',return_value=socket),patch.object(launch.time,'sleep'),patch.object(launch.subprocess,'Popen') as spawn:
            self.assertIsNone(launch.ensure_server())
            spawn.assert_not_called()

    def test_browser_only_opens_after_successful_readiness(self):
        with patch('sys.argv',['launch.py']),patch.object(launch,'ensure_server') as ensure,patch.object(launch.webbrowser,'open') as browser:
            self.assertEqual(launch.main(),0)
            ensure.assert_called_once_with(4317)
            browser.assert_called_once_with('http://127.0.0.1:4317/')

    def test_startup_does_not_open_browser(self):
        with patch('sys.argv',['launch.py','--no-browser']),patch.object(launch,'ensure_server'),patch.object(launch.webbrowser,'open') as browser:
            self.assertEqual(launch.main(),0)
            browser.assert_not_called()
