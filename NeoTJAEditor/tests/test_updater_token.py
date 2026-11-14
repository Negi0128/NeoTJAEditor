"""非公開リポジトリ向けの更新(鍵つきの取得)の確認。

GitHub へは繋がない。urllib の opener を偽物に差し替えて、送った要求を見る。"""
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from email.message import Message
from unittest.mock import patch

from neotja import updater

TOKEN = 'github_pat_TESTONLY0123456789abcdef'
ASSET_API = updater._ASSET_API_PREFIX + '12345'
BROWSER = 'https://github.com/Negi0128/NeoTJAEditor/releases/download/v1/NeoTJAEditor.exe'


class FakeResponse(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.headers = Message()
        self.headers['Content-Length'] = str(len(body))
        self.length = len(body)


class FakeOpener:
    """送られた Request を貯め、決めておいた順に応答(または例外)を返す。"""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)


def http_error(url, code, reason='Not Found'):
    return urllib.error.HTTPError(url, code, reason, Message(), None)


def auth_of(req):
    return req.get_header('Authorization') or req.unredirected_hdrs.get('Authorization')


class UpdaterTokenTests(unittest.TestCase):
    def run_with(self, token, answers, fn):
        opener = FakeOpener(answers)
        with patch.object(updater, '_TOKEN', token), \
             patch('urllib.request.build_opener', return_value=opener):
            result = fn()
        return result, opener.requests

    def test_without_token_sends_no_authorization(self):
        data, reqs = self.run_with('', [json.dumps({'tag_name': 'v1'}).encode()],
                                   lambda: updater.fetch_json(updater.RELEASES_API_URL))
        self.assertEqual(data['tag_name'], 'v1')
        self.assertEqual(len(reqs), 1)
        self.assertIsNone(auth_of(reqs[0]))
        self.assertEqual(reqs[0].get_header('X-github-api-version'), updater._API_VERSION)
        with patch.object(updater, '_TOKEN', ''):
            self.assertEqual(updater.asset_url({'url': ASSET_API, 'browser_download_url': BROWSER}),
                             BROWSER)

    def test_with_token_sends_bearer_and_uses_api_asset_url(self):
        _, reqs = self.run_with(TOKEN, [b'{}'],
                                lambda: updater.fetch_json(updater.RELEASES_API_URL))
        self.assertEqual(auth_of(reqs[0]), 'Bearer ' + TOKEN)
        # 転送先へ持っていかない側に入っていること
        self.assertIn('Authorization', reqs[0].unredirected_hdrs)
        self.assertNotIn('Authorization', reqs[0].headers)
        self.assertEqual(reqs[0].get_header('X-github-api-version'), updater._API_VERSION)
        with patch.object(updater, '_TOKEN', TOKEN):
            self.assertEqual(updater.asset_url({'url': ASSET_API, 'browser_download_url': BROWSER}),
                             ASSET_API)

    def test_asset_download_uses_octet_stream(self):
        def download():
            with updater.open_asset(ASSET_API, 5) as resp:
                return resp.read()
        body, reqs = self.run_with(TOKEN, [b'MZdata'], download)
        self.assertEqual(body, b'MZdata')
        self.assertEqual(reqs[0].full_url, ASSET_API)
        self.assertEqual(reqs[0].get_header('Accept'), 'application/octet-stream')
        self.assertEqual(auth_of(reqs[0]), 'Bearer ' + TOKEN)

    def test_redirect_drops_authorization(self):
        req = urllib.request.Request(ASSET_API, headers={'Accept': 'application/octet-stream',
                                                         'Authorization': 'Bearer ' + TOKEN})
        req.add_unredirected_header('Authorization', 'Bearer ' + TOKEN)
        new = updater._DropAuthRedirect().redirect_request(
            req, None, 302, 'Found', Message(),
            'https://objects.githubusercontent.com/signed?x=1')
        self.assertEqual(new.full_url, 'https://objects.githubusercontent.com/signed?x=1')
        self.assertIsNone(auth_of(new))
        self.assertNotIn(TOKEN, repr(new.headers) + repr(new.unredirected_hdrs))

    def test_falls_back_to_public_on_404(self):
        data, reqs = self.run_with(
            TOKEN, [http_error(updater.RELEASES_API_URL, 404), b'{"tag_name": "v2"}'],
            lambda: updater.fetch_json(updater.RELEASES_API_URL))
        self.assertEqual(data['tag_name'], 'v2')
        self.assertEqual(len(reqs), 2)
        self.assertEqual(auth_of(reqs[0]), 'Bearer ' + TOKEN)
        self.assertIsNone(auth_of(reqs[1]))

    def test_other_errors_do_not_fall_back(self):
        with self.assertRaises(IOError):
            self.run_with(TOKEN, [http_error(updater.RELEASES_API_URL, 500, 'Server Error')],
                          lambda: updater.fetch_json(updater.RELEASES_API_URL))

    def test_token_is_scrubbed_from_errors(self):
        leak = urllib.error.URLError('refused: Bearer ' + TOKEN)
        with self.assertRaises(IOError) as ctx:
            self.run_with(TOKEN, [leak], lambda: updater.fetch_json(updater.RELEASES_API_URL))
        self.assertNotIn(TOKEN, str(ctx.exception))
        with self.assertRaises(IOError) as ctx:
            self.run_with(TOKEN, [http_error(updater.RELEASES_API_URL, 404, 'x ' + TOKEN),
                                  http_error(updater.RELEASES_API_URL, 404, 'y ' + TOKEN)],
                          lambda: updater.fetch_json(updater.RELEASES_API_URL))
        self.assertNotIn(TOKEN, str(ctx.exception))
        with patch.object(updater, '_TOKEN', TOKEN):
            self.assertNotIn(TOKEN, updater.scrub('oops ' + TOKEN))
        # 鍵が積まれていなくても、Bearer の後ろは消す
        with patch.object(updater, '_TOKEN', ''):
            self.assertNotIn(TOKEN, updater.scrub('Authorization: Bearer ' + TOKEN))

    def test_download_worker_reports_scrubbed_failure(self):
        worker = updater.UpdateDownloadWorker(ASSET_API)
        failures = []
        worker.failed.connect(failures.append)
        answers = [urllib.error.URLError('boom ' + TOKEN)] * updater.DOWNLOAD_TRIES
        with patch.object(updater, 'RETRY_WAIT', 0):
            self.run_with(TOKEN, answers, worker.run)
        self.assertEqual(len(failures), 1)
        self.assertNotIn(TOKEN, failures[0])

    def test_build_writes_and_removes_token_module(self):
        from build_version import TOKEN_ENV, write_update_token
        with tempfile.TemporaryDirectory() as root, \
             patch.dict(os.environ, {TOKEN_ENV: ''}):
            os.makedirs(os.path.join(root, 'neotja'))
            module = os.path.join(root, 'neotja', '_update_token.py')
            # 鍵ファイルが無くても組み立ては通る
            self.assertFalse(write_update_token(root))
            self.assertFalse(os.path.exists(module))
            with open(os.path.join(root, 'update_token.txt'), 'w', encoding='utf-8') as f:
                f.write('# コメント行\n' + TOKEN + '\n')
            self.assertTrue(write_update_token(root))
            with open(module, encoding='utf-8') as f:
                text = f.read()
            self.assertNotIn(TOKEN, text)
            ns = {}
            exec(text, ns)
            self.assertEqual(updater._decode_token(ns['DATA'], ns['KEY']), TOKEN)
            # 鍵ファイルを外したら、前回の生成物も消える
            os.remove(os.path.join(root, 'update_token.txt'))
            self.assertFalse(write_update_token(root))
            self.assertFalse(os.path.exists(module))


if __name__ == '__main__':
    unittest.main()
