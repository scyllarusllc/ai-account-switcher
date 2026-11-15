import unittest
from unittest.mock import patch
import chrome_profiles as app

class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.config = {'profiles': {'work': {'user_data_dir': '~/Chrome Work', 'proxy': 'http://127.0.0.1:7890', 'args': ['--incognito'], 'urls': ['https://www.google.com']}}}

    @patch.object(app, 'chrome_binary', return_value='/chrome')
    def test_paths_proxy_and_url_override(self, _):
        command = app.build_command(self.config, 'work', ['https://example.com'], [])
        self.assertTrue(command[1].endswith('/Chrome Work'))
        self.assertIn('--proxy-server=http://127.0.0.1:7890', command)
        self.assertEqual(command[-1], 'https://example.com')
        self.assertNotIn('https://www.google.com', command)

    @patch.object(app, 'chrome_binary', return_value='/chrome')
    def test_conflicting_directory_rejected(self, _):
        with self.assertRaises(ValueError):
            app.build_command(self.config, 'work', [], ['--user-data-dir=/other'])

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):
            app.build_command(self.config, 'missing', [], [])

    @patch.object(app, 'chrome_binary', return_value='/chrome')
    def test_non_web_targets_rejected(self, _):
        with self.assertRaises(ValueError):
            app.build_command(self.config, 'work', ['--something'], [])

if __name__ == '__main__':
    unittest.main()
