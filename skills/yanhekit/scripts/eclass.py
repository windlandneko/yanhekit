"""BIT eclass (zy-eclass.bit.edu.cn) — homework and exams beside Yanhekt.

Import an existing eclass browser session or a Netscape cookie export. Its
cookies stay separate from Yanhekt's backend SSO session.
"""

import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta, timezone
from http.cookiejar import LoadError, MozillaCookieJar
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from client import CONFIG, USER_AGENT, describe

try:
  import browser_cookie3  # optional; Chrome / Safari / Firefox cookie readers
except ImportError:
  browser_cookie3 = None

ORIGIN = 'https://zy-eclass.bit.edu.cn'
COOKIE_PATH = CONFIG / 'eclass-cookies.txt'
ECLASS_HOST_RE = re.compile(r'(^|\.)zy-eclass\.bit\.edu\.cn$|(\.|^)eclass\.bit\.edu\.cn$')
BROWSERS = ['chrome', 'chromium', 'edge', 'brave', 'firefox', 'opera', 'safari']


class EclassError(Exception):
  """A failure to show to the user as is."""


class Eclass:
  def __init__(self):
    CONFIG.mkdir(parents=True, exist_ok=True, mode=0o700)
    self.session = requests.Session()
    self.session.headers.update({'User-Agent': USER_AGENT, 'Referer': ORIGIN + '/'})
    retry = Retry(3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    self.session.mount('https://', HTTPAdapter(max_retries=retry))
    self.session.cookies = MozillaCookieJar(str(COOKIE_PATH))
    self.cookie_error = None
    if COOKIE_PATH.exists():
      try:
        self.session.cookies.load(ignore_discard=True, ignore_expires=True)
      except (LoadError, OSError):
        self.session.cookies.clear()
        self.cookie_error = '保存的 cookie 文件损坏或无法读取，请重新导入。'
    self.profile = None
    self.errors = []

  # ---------------------------------------------------------------- auth

  def authenticate(self):
    """Use saved eclass cookies if the server still accepts them."""
    self.profile = None
    response = self.session.get(ORIGIN + '/api/profile', timeout=30, allow_redirects=False)
    if response.status_code in (401, 403) or response.is_redirect:
      return False
    response.raise_for_status()
    try:
      data = response.json()
    except ValueError:
      return False
    if not isinstance(data, dict) or not data.get('active'):
      return False
    self.profile = data
    return True

  # ---------------------------------------------------------------- API

  def _get(self, path, **params):
    response = self.session.get(ORIGIN + path, params=params, timeout=30, allow_redirects=False)
    if response.status_code in (401, 403) or response.is_redirect:
      raise EclassError('学习平台登录态失效或无权访问，请重新导入 cookie。')
    response.raise_for_status()
    try:
      data = response.json()
    except ValueError as error:
      raise EclassError(f'{path}: 学习平台未返回有效 JSON') from error
    if not isinstance(data, dict):
      raise EclassError(f'{path}: 学习平台响应格式错误')
    return data

  def _list(self, path, key, **params):
    rows = self._get(path, **params).get(key)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
      raise EclassError(f'{path}: 缺少有效的 {key} 列表')
    return rows

  def profile_get(self):
    return self._get('/api/profile')

  def courses(self, term=None, all_terms=False):
    courses = self._list('/api/my-courses', 'courses')
    if all_terms or not courses:
      return courses
    terms = {(c.get('semester') or {}).get('code') for c in courses} - {None, ''}
    if term is None:
      if not terms:
        raise EclassError('课程没有学期信息，请使用 --all-terms。')
      term = max(terms, key=lambda value: tuple(int(n) for n in re.findall(r'\d+', value)))
    if term not in terms:
      raise EclassError(f'没有 {term} 学期的课程，可选学期: {", ".join(sorted(terms))}')
    return [c for c in courses if (c.get('semester') or {}).get('code') == term]

  def activities(self, course_id, sub_course_id=0):
    return self._list(f'/api/courses/{course_id}/activities', 'activities', sub_course_id=sub_course_id)

  def activity(self, activity_id):
    return self._get(f'/api/activities/{activity_id}')

  def exams(self, course_id):
    return self._list(f'/api/courses/{course_id}/exams', 'exams', **{'no-intercept': 'true'})

  def homework(self, course_id):
    return [a for a in self.activities(course_id) if a.get('type') == 'homework']

  def pending(self, term=None, all_terms=False, include_all=False):
    """Unfinished homework and exams; errors mark incomplete results."""
    rows = []
    self.errors = []
    now = datetime.now(UTC)
    for course in self.courses(term, all_terms):
      cid = course['id']
      cname = course['name']
      for kind, fetch in [('homework', self.homework), ('exam', self.exams)]:
        try:
          items = fetch(cid)
        except (requests.RequestException, EclassError) as error:
          self.errors.append(f'{cname} ({cid}) {kind}: {describe(error)}')
          continue
        for item in items:
          if not include_all and _closed(item, now):
            continue
          detail_failed = False
          if kind == 'homework':
            try:
              item = {**item, **self.activity(item['id'])}
            except (requests.RequestException, EclassError) as error:
              self.errors.append(f'{cname} ({cid}) homework {item["id"]}: {describe(error)}')
              item = {**item, 'user_submit_count': None}
              detail_failed = True
          row = {**item, 'kind': kind, 'course_id': cid, 'course': cname}
          row['status'] = _status(row, now)
          if row['status'] == 'unknown' and not detail_failed:
            self.errors.append(f'{cname} ({cid}) {kind} {item["id"]}: 提交状态未知')
          if include_all or row['status'] not in ('closed', 'submitted'):
            rows.append(row)
    rows.sort(key=lambda r: _time(r.get('end_time')) or datetime.max.replace(tzinfo=UTC))
    return rows

  # ---------------------------------------------------------------- import

  def import_from_browser(self, browser='chrome'):
    """Read zy-eclass cookies from a browser cookie DB. Returns count saved."""
    if browser_cookie3 is None:
      raise EclassError(
        '未安装 browser-cookie3。请使用 uv run 运行 yanhekit，或用 '
        '`yanhekit eclass import --file <cookies.txt>` 导入 Netscape 格式。'
      )

    readers = {
      'chrome': browser_cookie3.chrome,
      'chromium': browser_cookie3.chromium,
      'edge': browser_cookie3.edge,
      'brave': browser_cookie3.brave,
      'firefox': browser_cookie3.firefox,
      'opera': browser_cookie3.opera,
      'safari': browser_cookie3.safari,
    }
    targets = BROWSERS if browser == 'all' else [browser]
    failures = []
    for name in targets:
      reader = readers.get(name)
      if reader is None:
        continue
      jar = MozillaCookieJar(str(COOKIE_PATH))
      try:
        cookies = list(reader(domain_name='zy-eclass.bit.edu.cn'))
      except Exception as error:  # noqa: BLE001 - browser readers have different platform-specific errors
        failures.append(f'{name}: {type(error).__name__}')
        continue
      for cookie in cookies:
        # browser-cookie3 may return cookies from related domains (.bit.edu.cn)
        if cookie.domain and not ECLASS_HOST_RE.search(cookie.domain):
          continue
        jar.set_cookie(cookie)
      if not list(jar):
        continue
      try:
        return self._import(jar)
      except EclassError as error:
        failures.append(f'{name}: {error}')
    hint = '没有可用的 eclass cookie。请先在所选浏览器打开 https://zy-eclass.bit.edu.cn 登录。'
    if failures:
      hint += f'\n浏览器导入失败: {"; ".join(failures)}'
    raise EclassError(hint)

  def import_from_netscape(self, path):
    """Load a Netscape-format cookies file (e.g. from a cookie-editor export)."""
    src = Path(path).expanduser().resolve()
    if not src.exists():
      raise EclassError(f'文件不存在: {src}')
    jar = MozillaCookieJar(str(src))
    try:
      jar.load(ignore_discard=True, ignore_expires=True)
    except (LoadError, OSError) as error:
      raise EclassError('无法读取 Netscape cookie 文件，请检查文件格式和权限。') from error
    kept = MozillaCookieJar(str(COOKIE_PATH))
    for cookie in jar:
      if cookie.domain and ECLASS_HOST_RE.search(cookie.domain):
        kept.set_cookie(cookie)
    if not list(kept):
      raise EclassError('该文件中没有 zy-eclass.bit.edu.cn 的 cookie')
    return self._import(kept)

  def _import(self, jar):
    """Validate the candidate session before atomically replacing saved cookies."""
    previous, profile = self.session.cookies, self.profile
    self.session.cookies = jar
    try:
      if not self.authenticate():
        raise EclassError('导入的 cookie 已过期或无效，原登录态已保留。')
      fd, name = tempfile.mkstemp(prefix='.eclass-', dir=CONFIG)
      os.close(fd)
      try:
        jar.save(name, ignore_expires=True, ignore_discard=True)
        os.replace(name, COOKIE_PATH)
      finally:
        Path(name).unlink(missing_ok=True)
    except (EclassError, requests.RequestException, OSError):
      self.session.cookies, self.profile = previous, profile
      raise
    self.cookie_error = None
    return len(list(jar))


def capture():
  """Best-effort saved-session check and Chrome import, independent of Yanhekt SSO."""
  try:
    client = Eclass()
    if not client.authenticate():
      client.import_from_browser('chrome')
    print('Eclass: signed in.', flush=True)
  except (EclassError, requests.RequestException, OSError) as error:
    print(f'Eclass: {describe(error)}', file=sys.stderr, flush=True)
    print('运行 `yanhekit eclass import --browser <browser>` 或 `--file <cookies.txt>` 重试。', file=sys.stderr)


def _time(value):
  if not value:
    return None
  try:
    dt = datetime.fromisoformat(value)
  except (TypeError, ValueError) as error:
    raise EclassError('学习平台返回了无效的时间字段。') from error
  return dt if dt.tzinfo else dt.replace(tzinfo=timezone(timedelta(hours=8)))


def _closed(item, now):
  end = _time(item.get('end_time'))
  return bool(item.get('is_closed') or (end is not None and end <= now))


def _status(item, now):
  if _closed(item, now):
    return 'closed'
  if item['kind'] == 'homework':
    count = item.get('user_submit_count')
    submitted = None
    if isinstance(count, int) and count >= 0:
      submitted = True if count > 0 else (None if item.get('submit_by_group') else False)
  else:
    submissions = item.get('exam_submissions')
    submitted = None
    if isinstance(submissions, list) and all(isinstance(s, dict) for s in submissions):
      if any(s.get('submitted_at') or s.get('is_submitted') is True for s in submissions):
        submitted = True
      elif not item.get('submit_by_group') and (
        not submissions or all(s.get('is_submitted') is False for s in submissions)
      ):
        submitted = False
  if submitted is None:
    return 'unknown'
  if submitted:
    return 'submitted'
  start = _time(item.get('start_time'))
  return 'upcoming' if item.get('is_started') is False or (start is not None and start > now) else 'pending'


# ---------------------------------------------------------------- CLI


def add_parser(subparsers):
  p = subparsers.add_parser(
    'eclass',
    help='Access zy-eclass (BIT 学习平台): courses, activities, homework, exams',
  )
  sub = p.add_subparsers(dest='eclass_command', required=True)

  sub.add_parser('status', help='Show whether the saved eclass cookies still work')

  imp = sub.add_parser('import', help='Capture eclass cookies from a browser or a Netscape file')
  imp.add_argument(
    '--browser',
    choices=[*BROWSERS, 'all'],
    default='chrome',
    help='Browser cookie DB to read (default: chrome)',
  )
  imp.add_argument('--file', help='Netscape-format cookies file (cookie-editor export)')

  courses = sub.add_parser('courses', help="List this user's enrolled courses (default: latest term)")

  acts = sub.add_parser('activities', help='List activities for a course')
  acts.add_argument('course_id', help='Course ID from `eclass courses`')
  acts.add_argument('--all', action='store_true', help='Include closed activities')
  acts.add_argument('--json', action='store_true', help='Print full JSON')

  one = sub.add_parser('activity', help='Show one activity in detail (includes user_submit_count)')
  one.add_argument('activity_id', type=int)

  exams = sub.add_parser('exams', help='List exams for a course')
  exams.add_argument('course_id')

  hw = sub.add_parser('homework', help='List homework activities for a course')
  hw.add_argument('course_id')

  pending = sub.add_parser('pending', help='Unfinished homework and exams (default: latest term)')
  pending.add_argument('--all', action='store_true', help='Also show submitted and closed items')
  pending.add_argument('--json', action='store_true', help='Print items, errors and completeness as JSON')
  for listing in (courses, pending):
    scope = listing.add_mutually_exclusive_group()
    scope.add_argument('--term', help='Eclass semester code, such as 2026-1 (default: latest available)')
    scope.add_argument('--all-terms', action='store_true', help='Include all enrolled semesters')

  return p


def _stamp(value):
  dt = _time(value)
  return dt.astimezone().strftime('%Y-%m-%d %H:%M') if dt else '—'


def dispatch(args, *, out=None, err=None):
  out = sys.stdout if out is None else out
  err = sys.stderr if err is None else err
  client = Eclass()

  if args.eclass_command == 'status':
    if not COOKIE_PATH.exists():
      print('尚未登录: ~/.config/yanhekit/eclass-cookies.txt 不存在', file=out)
      print('运行 `yanhekit login` 或 `yanhekit eclass import` 即可。', file=out)
      return 1
    if not client.authenticate():
      if client.cookie_error:
        print(client.cookie_error, file=out)
      print('cookie 已过期或无效。请重新运行 `yanhekit login` 或 `yanhekit eclass import`。', file=out)
      return 1
    p = client.profile or {}
    dept = (p.get('department') or {}).get('name', '—')
    print(f'Signed in as {p.get("name", "?")} ({p.get("id", "?")}), {dept}', file=out)
    return 0

  if args.eclass_command == 'import':
    if args.file:
      count = client.import_from_netscape(args.file)
      src = f'file {args.file}'
    else:
      count = client.import_from_browser(args.browser)
      src = f'browser {args.browser}'
    p = client.profile or {}
    print(
      f'Imported {count} cookies from {src}. Signed in as {p.get("name", "?")} ({p.get("id", "?")}).',
      file=out,
    )
    return 0

  if args.eclass_command != 'pending' and not client.authenticate():
    print('尚未登录或 cookie 已过期。请运行 `yanhekit login` 或 `yanhekit eclass import`。', file=out)
    return 1

  if args.eclass_command == 'courses':
    for c in client.courses(args.term, args.all_terms):
      teacher = (c.get('instructors') or [{}])[0].get('name', '—')
      sem = (c.get('semester') or {}).get('code', '—')
      print(f'{c["id"]:6}  {c["name"]:30}  {teacher}  ({sem})', file=out)
    return 0

  if args.eclass_command == 'activities':
    acts = client.activities(args.course_id)
    if not args.all:
      acts = [a for a in acts if not a.get('is_closed')]
    if args.json:
      json.dump(acts, out, ensure_ascii=False, indent=2)
      out.write('\n')
      return 0
    for a in acts:
      end = _stamp(a.get('end_time'))
      closed = 'closed' if a.get('is_closed') else 'open'
      print(f'{a["id"]:6}  {a.get("type", "?"):14}  end={end}  {closed:6}  {a.get("title", "")}', file=out)
    return 0

  if args.eclass_command == 'activity':
    json.dump(client.activity(args.activity_id), out, ensure_ascii=False, indent=2)
    out.write('\n')
    return 0

  if args.eclass_command == 'exams':
    for e in client.exams(args.course_id):
      print(
        f'{e.get("id", "?"):6}  {e.get("title", "")} '
        f'start={_stamp(e.get("start_time"))}  end={_stamp(e.get("end_time"))}',
        file=out,
      )
    return 0

  if args.eclass_command == 'homework':
    for a in client.homework(args.course_id):
      end = _stamp(a.get('end_time'))
      closed = 'closed' if a.get('is_closed') else 'open'
      print(f'{a["id"]:6}  end={end}  {closed:6}  {a.get("title", "")}', file=out)
    return 0

  if args.eclass_command == 'pending':
    try:
      if not client.authenticate():
        raise EclassError('尚未登录或 cookie 已过期。请运行 `yanhekit login` 或 `yanhekit eclass import`。')
      rows = client.pending(args.term, args.all_terms, args.all)
    except (EclassError, requests.RequestException) as error:
      rows = []
      client.errors.append(describe(error))
    if args.json:
      json.dump(
        {'complete': not client.errors, 'items': rows, 'errors': client.errors}, out, ensure_ascii=False, indent=2
      )
      out.write('\n')
    else:
      for r in rows:
        count = r.get('user_submit_count')
        submitted = f'  submitted={count if count is not None else "?"}' if r['kind'] == 'homework' else ''
        print(
          f'{r["course_id"]:6}  {r["course"]}  {r["kind"]:8}  {r["status"]:9} '
          f'start={_stamp(r.get("start_time"))}  end={_stamp(r.get("end_time"))}{submitted}  {r.get("title", "")}',
          file=out,
        )
      if not rows and not client.errors:
        print('所选学期没有未完成的作业或考试。', file=out)
    if client.errors:
      print('查询结果不完整，请检查以下课程或事项：', file=err)
      for error in client.errors:
        print(f'  {error}', file=err)
    return int(bool(client.errors))

  return 1
