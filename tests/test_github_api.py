import io
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
from github_api import APIError, GitHub


class GitHubTests(unittest.TestCase):
    def setUp(self):
        with patch.dict('os.environ', {'GITHUB_APP_ID': '1', 'GITHUB_INSTALLATION_ID': '2'}, clear=True):
            self.api = GitHub()
        self.api.auth = lambda: 'test-token'

    def test_network_error_is_redacted_and_recoverable(self):
        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError('sensitive connection detail')):
            with self.assertRaises(APIError) as caught:
                self.api.request('GET', '/installation/repositories')
        self.assertEqual(caught.exception.status, 0)
        self.assertNotIn('sensitive', str(caught.exception))

    def test_rate_limit_preserves_backoff_without_response_body(self):
        error = urllib.error.HTTPError('https://api.github.com/test', 429, 'limited',
                                      {'Retry-After': '3600'}, io.BytesIO(b'secret response'))
        with patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(APIError) as caught:
                self.api.request('GET', '/test?secret=query')
        self.assertEqual(caught.exception.retry_after, 3600)
        self.assertNotIn('secret', str(caught.exception))

    def test_not_modified_returns_cached_page(self):
        self.api.cache['/test'] = ('etag', {'runners': [{'id': 1}]})
        error = urllib.error.HTTPError('https://api.github.com/test', 304, 'unchanged', {}, None)
        with patch('urllib.request.urlopen', side_effect=error) as request:
            self.assertEqual(self.api.request('GET', '/test', cached=True), {'runners': [{'id': 1}]})
        self.assertEqual(request.call_args.args[0].get_header('If-none-match'), 'etag')

    def test_pagination_reads_all_pages(self):
        with patch.object(self.api, 'request', side_effect=[{'jobs': list(range(100))}, {'jobs': [100]}]) as request:
            self.assertEqual(list(self.api.pages('/jobs?filter=latest', 'jobs')), list(range(101)))
        self.assertIn('page=2', request.call_args.args[1])

    def test_rejected_installation_token_is_refreshed_next_poll(self):
        self.api.token_until = 9999999999
        error = urllib.error.HTTPError('https://api.github.com/test', 401, 'expired', {}, None)
        with patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(APIError):
                self.api.request('GET', '/test')
        self.assertEqual(self.api.token_until, 0)

    def test_external_paths_are_rejected_before_authentication(self):
        for path in ('https://example.com', '//example.com'):
            with self.assertRaises(ValueError):
                self.api.request('GET', path)


if __name__ == '__main__':
    unittest.main()
