"""Regression checks with synthetic cookies and mocked network, keyring and browser."""

import argparse
import contextlib
import io
import json
import stat
import tempfile
import unittest
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import eclass
import server
import yanhekit


class EclassTests(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.config = Path(self.directory.name)
    self.cookie_path = self.config / 'cookies.txt'
    for target, value in [('CONFIG', self.config), ('COOKIE_PATH', self.cookie_path)]:
      context = patch.object(eclass, target, value)
      context.start()
      self.addCleanup(context.stop)
    network = patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected network access'))
    network.start()
    self.addCleanup(network.stop)
    self.client = eclass.Eclass()

  def response(self, data=None, status=200):
    response = requests.Response()
    response.status_code = status
    response.url = eclass.ORIGIN + '/api/profile'
    response._content = json.dumps(data).encode()
    return response

  def jar(self, value, filename=None):
    jar = MozillaCookieJar(str(filename or self.cookie_path))
    jar.set_cookie(requests.cookies.create_cookie('role_token', value, domain='zy-eclass.bit.edu.cn'))
    return jar

  def setup_pending(self, homework, exams):
    self.client.courses = Mock(return_value=[{'id': 1, 'name': 'Synthetic course'}])
    self.client.homework = Mock(return_value=homework)
    self.client.exams = Mock(return_value=exams)

  def test_file_import_updates_session_and_failed_import_preserves_it(self):
    source = self.jar('new', self.config / 'export.txt')
    source.set_cookie(requests.cookies.create_cookie('other', 'excluded', domain='other.example'))
    source.save(ignore_discard=True)
    self.client.session.get = Mock(return_value=self.response({'active': True}))
    self.assertEqual(self.client.import_from_netscape(source.filename), 1)
    self.assertEqual(next(iter(self.client.session.cookies)).value, 'new')
    persisted = self.cookie_path.read_bytes()
    self.assertEqual(stat.S_IMODE(self.cookie_path.stat().st_mode), 0o600)
    source = self.jar('invalid', source.filename)
    source.save(ignore_discard=True)
    self.client.session.get.return_value = self.response({'active': False})
    with self.assertRaises(eclass.EclassError):
      self.client.import_from_netscape(source.filename)
    self.assertEqual(next(iter(self.client.session.cookies)).value, 'new')
    self.assertEqual(self.client.profile, {'active': True})
    self.assertEqual(self.cookie_path.read_bytes(), persisted)
    self.client.session.get.side_effect = requests.ConnectionError()
    with self.assertRaises(requests.ConnectionError):
      self.client.import_from_netscape(source.filename)
    self.assertEqual(next(iter(self.client.session.cookies)).value, 'new')
    self.assertEqual(self.cookie_path.read_bytes(), persisted)

  def test_browser_all_uses_first_valid_session_without_mixing_accounts(self):
    readers = Mock()
    for name in eclass.BROWSERS:
      getattr(readers, name).return_value = []
    readers.chrome.return_value = list(self.jar('invalid-chrome'))
    readers.firefox.return_value = list(self.jar('valid-firefox'))
    readers.safari.return_value = list(self.jar('other-account'))
    self.client.session.get = Mock(side_effect=[self.response({'active': False}), self.response({'active': True})])
    with patch.object(eclass, 'browser_cookie3', readers):
      self.assertEqual(self.client.import_from_browser('all'), 1)
    self.assertEqual(next(iter(self.client.session.cookies)).value, 'valid-firefox')
    readers.safari.assert_not_called()

  def test_corrupt_cookie_file_can_be_replaced_and_errors_do_not_expose_it(self):
    self.cookie_path.write_text('not-a-cookie-file synthetic-private-content', encoding='utf-8')
    client = eclass.Eclass()
    self.assertTrue(client.cookie_error)
    self.assertEqual(len(client.session.cookies), 0)
    source = self.jar('valid', self.config / 'export.txt')
    source.save(ignore_discard=True)
    client.session.get = Mock(return_value=self.response({'active': True}))
    client.import_from_netscape(source.filename)
    self.assertIsNone(client.cookie_error)
    with self.assertRaises(eclass.EclassError) as failure:
      client.import_from_netscape(self.config / 'missing.txt')
    self.assertNotIn('synthetic-private-content', str(failure.exception))

  def test_authentication_distinguishes_rejection_from_server_failure(self):
    self.client.session.get = Mock(return_value=self.response(status=401))
    self.assertFalse(self.client.authenticate())
    self.client.session.get.return_value = self.response(status=500)
    with self.assertRaises(requests.HTTPError):
      self.client.authenticate()
    self.client.session.get.return_value = self.response(['unexpected-profile'])
    self.assertFalse(self.client.authenticate())
    self.client.session.get.return_value = self.response({'active': True})
    self.assertTrue(self.client.authenticate())
    self.assertFalse(self.cookie_path.exists())
    self.client.session.get.return_value = self.response({})
    with self.assertRaises(eclass.EclassError):
      self.client.courses()

  def test_latest_term_explicit_term_and_all_terms(self):
    courses = [
      {'id': 1, 'semester': {'code': '2025-2'}},
      {'id': 2, 'semester': {'code': '2026-1'}},
      {'id': 3, 'semester': {'code': '2026-1'}},
    ]
    self.client.session.get = Mock(return_value=self.response({'courses': courses}))
    self.assertEqual([c['id'] for c in self.client.courses()], [2, 3])
    self.assertEqual([c['id'] for c in self.client.courses('2025-2')], [1])
    self.assertEqual(self.client.courses(all_terms=True), courses)
    with self.assertRaises(eclass.EclassError):
      self.client.courses('2099-1')

  def test_pending_filters_and_sorts_absolute_deadlines_with_undated_last(self):
    homework = [
      {'id': 1, 'end_time': '2049-12-31T23:00:00Z'},
      {'id': 2, 'end_time': '2049-12-31T23:00:00Z'},
      {'id': 3, 'is_closed': True},
      {'id': 4, 'end_time': '2020-01-01T00:00:00Z'},
      {'id': 5},
      {'id': 6, 'end_time': '2050-01-01T00:30:00+08:00'},
    ]
    exams = [
      {'id': 10, 'exam_submissions': [], 'end_time': '2050-01-02T00:00:00Z'},
      {'id': 11, 'exam_submissions': [{'is_submitted': True}]},
    ]
    self.setup_pending(homework, exams)
    self.client.activity = Mock(side_effect=lambda identity: {'user_submit_count': 1 if identity == 2 else 0})
    rows = self.client.pending('2026-1')
    self.assertEqual([r['id'] for r in rows], [6, 1, 10, 5])
    self.assertTrue(all(r['status'] == 'pending' for r in rows))
    self.assertEqual(self.client.errors, [])
    self.client.courses.assert_called_once_with('2026-1', False)
    self.assertEqual(self.client.activity.call_count, 4)
    history = self.client.pending(include_all=True)
    self.assertEqual(len(history), 8)
    self.assertEqual(next(r for r in history if r['id'] == 2)['status'], 'submitted')
    self.assertEqual(next(r for r in history if r['id'] == 4)['status'], 'closed')

  def test_unknown_group_or_exam_status_is_not_reported_as_completed(self):
    self.setup_pending(
      [{'id': 1, 'submit_by_group': True}],
      [
        {'id': 2, 'exam_submissions': [{'state': 'started'}]},
        {'id': 3, 'start_time': '2050-01-01', 'exam_submissions': []},
      ],
    )
    self.client.activity = Mock(return_value={'user_submit_count': 0})
    rows = self.client.pending()
    self.assertEqual([r['status'] for r in rows], ['unknown', 'unknown', 'upcoming'])
    self.assertEqual(len(self.client.errors), 2)

  def test_course_and_detail_failures_keep_other_results_and_signal_incompleteness(self):
    self.setup_pending([], [{'id': 10, 'exam_submissions': []}])
    self.client.homework.side_effect = requests.HTTPError(response=self.response(status=503))
    rows = self.client.pending()
    self.assertEqual([r['id'] for r in rows], [10])
    self.client.exams.assert_called_once_with(1)
    self.assertEqual(len(self.client.errors), 1)
    self.client.homework.side_effect = None
    self.client.homework.return_value = [{'id': 1}]
    self.client.activity = Mock(side_effect=requests.Timeout())
    self.assertEqual(self.client.pending()[0]['status'], 'unknown')
    self.assertEqual(len(self.client.errors), 1)
    out, err = io.StringIO(), io.StringIO()
    args = argparse.Namespace(eclass_command='pending', term=None, all_terms=False, all=False, json=True)
    self.client.authenticate = Mock(return_value=True)
    with patch.object(eclass, 'Eclass', return_value=self.client):
      self.assertEqual(eclass.dispatch(args, out=out, err=err), 1)
    data = json.loads(out.getvalue())
    self.assertFalse(data['complete'])
    self.assertEqual(len(data['items']), 2)
    self.assertTrue(data['errors'])
    self.assertIn('不完整', err.getvalue())

  def test_login_captures_once_for_saved_and_new_sessions_and_can_skip(self):
    client = Mock()
    for saved in [True, False]:
      client.authenticate.return_value = saved
      with patch.object(yanhekit, 'Yanhekt', return_value=client), patch.object(server, 'serve') as page:
        with patch.object(eclass, 'capture') as capture:
          yanhekit.signed_in(capture_eclass=True)
          capture.assert_called_once_with()
          self.assertEqual(page.call_count, int(not saved))
        with patch.object(eclass, 'capture') as capture:
          yanhekit.signed_in(capture_eclass=False)
          capture.assert_not_called()
    self.client.authenticate = Mock(side_effect=requests.Timeout())
    with patch.object(eclass, 'Eclass', return_value=self.client), contextlib.redirect_stderr(io.StringIO()) as err:
      eclass.capture()
    self.assertIn('Network error', err.getvalue())

  def test_pending_json_stays_structured_for_login_and_course_list_failures(self):
    args = argparse.Namespace(eclass_command='pending', term=None, all_terms=False, all=False, json=True)
    for signed_in in [False, True]:
      out, err = io.StringIO(), io.StringIO()
      self.client.errors = []
      self.client.authenticate = Mock(return_value=signed_in)
      self.client.courses = Mock(side_effect=requests.Timeout())
      with patch.object(eclass, 'Eclass', return_value=self.client):
        self.assertEqual(eclass.dispatch(args, out=out, err=err), 1)
      data = json.loads(out.getvalue())
      self.assertFalse(data['complete'])
      self.assertEqual(data['items'], [])
      self.assertEqual(len(data['errors']), 1)
      self.assertIn('不完整', err.getvalue())

  def test_login_flags_and_term_scope(self):
    for option, expected in [('--eclass', True), ('--no-eclass', False)]:
      with patch.object(yanhekit.sys, 'argv', ['yanhekit.py', 'login', option]):
        self.assertEqual(yanhekit.parse().capture_eclass, expected)
    with (
      patch.object(yanhekit.sys, 'argv', ['yanhekit.py', 'eclass', 'pending', '--term', '2026-1', '--all-terms']),
      contextlib.redirect_stderr(io.StringIO()),
      self.assertRaises(SystemExit),
    ):
      yanhekit.parse()

  def test_sign_in_and_download_pages_start_and_keep_the_api_guard(self):
    for workspace in [None, self.config]:
      signin = Mock()
      signin.start.return_value = workspace is not None
      event = Mock()
      event.is_set.return_value = workspace is not None
      http = Mock(port=12345)
      client = Mock(_capture_eclass=False)
      client.courses.return_value = []
      with contextlib.ExitStack() as stack:
        for name, value in [('saved_username', ''), ('saved_password', None), ('SignIn', signin)]:
          stack.enter_context(patch.object(server, name, return_value=value))
        stack.enter_context(patch.object(server.threading, 'Event', return_value=event))
        stack.enter_context(patch.object(server.threading, 'Thread'))
        stack.enter_context(patch.object(server.State, 'flush'))
        stack.enter_context(patch.object(server, 'Downloads'))
        make = stack.enter_context(patch.object(server, 'make_server', return_value=http))
        open_page = stack.enter_context(patch.object(server.webbrowser, 'open'))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        server.serve(client, workspace)
        make.assert_called_once()
        open_page.assert_called_once()
        http.shutdown.assert_called_once()
        app = make.call_args.args[2]
        token = open_page.call_args.args[0].split('#')[1]
        web = app.test_client()
        self.assertEqual(web.get('/').status_code, 403)
        headers = {'Host': '127.0.0.1:12345'}
        self.assertEqual(web.post('/api/resend', headers=headers).status_code, 403)
        headers['X-Token'] = token
        self.assertEqual(web.post('/api/resend', headers=headers).status_code, 200)
        signin.send_code.assert_called_once()


if __name__ == '__main__':
  unittest.main()
