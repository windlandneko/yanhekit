"""Yanhekt session and course API."""

import hashlib
import html
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

ORIGIN = 'https://aita.yanhekt.cn'
CAMPUS_IP = '10.0.11.240'
CONFIG = Path.home() / '.config' / 'yanhekit'
USER_AGENT = (
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
)
STREAMS = {'classroom': '3', 'screen': '2'}  # video_list[].type
SORTS = {'newest': 'c.created_at desc', 'oldest': 'c.created_at asc', 'popular': 'c.click_record_num desc'}


class APIError(Exception):
  pass


# What ends a lecture's step, or a command, with a message from `describe` rather than a traceback
FAILURES = (APIError, OSError, RuntimeError, ValueError, subprocess.CalledProcessError)  # OSError covers requests


class Yanhekt:
  def __init__(self):
    CONFIG.mkdir(parents=True, exist_ok=True, mode=0o700)
    self.session = requests.Session()
    self.session.headers.update({'User-Agent': USER_AGENT, 'Referer': ORIGIN + '/'})
    retry = Retry(3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    self.session.mount('https://', HTTPAdapter(max_retries=retry))
    self.session.cookies = MozillaCookieJar(CONFIG / 'cookies.txt')
    if (CONFIG / 'cookies.txt').exists():
      self.session.cookies.load(ignore_discard=True)
    self.profile = None

  def authenticate(self):
    """Use the saved `_token` cookie if Yanhekt still accepts it."""
    token = next((c.value for c in self.session.cookies if c.name == '_token'), '')
    jwt = re.search(r'eyJ[\w-]+\.[\w-]+\.[\w-]+', unquote(token))
    if not jwt:
      return False
    self.session.headers['Authorization'] = 'Bearer ' + jwt[0]
    response = self.session.get(ORIGIN + '/userapi/v1/infosimple', timeout=30)
    body = response.json() if response.ok else {}
    if body.get('code') != 200:
      del self.session.headers['Authorization']
      return False
    self.profile = body['params']
    self.session.cookies.save(ignore_discard=True, ignore_expires=True)
    os.chmod(CONFIG / 'cookies.txt', 0o600)
    return True

  def get_json(self, path, headers=None, **params):
    response = self.session.get(ORIGIN + path, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()

  def get(self, path, headers=None, **params):
    body = self.get_json(path, headers, **params)
    if body['code'] not in (0, 10002):  # 10002: nothing recorded
      raise APIError(f'{path}: {body.get("msg") or body["code"]}')
    return body

  def courses(self):
    """Enrolled courses, newest term first."""
    courses, page = [], 1
    while True:
      params = {'force_mycourse': 1, 'nowpage': page, 'per-page': 100}
      result = self.get_json('/vlabpassportapi/v1/account-profile/course', **params)['params']['result']
      courses += [
        {'id': str(row['Id']), 'title': html.unescape(row['Title']), 'teacher': row['Teacher'], 'term': row['TermName']}
        for row in result['data']
      ]
      if not result['data'] or len(courses) >= result['total']:
        return sorted(courses, key=lambda course: course['term'], reverse=True)  # 2026-2027-1; stable within a term
      page += 1

  def search(self, keyword='', term=None, college=None, sort=None, page=1, per_page=50):
    """One page of the platform's course search across every term and college: (total, courses)."""
    tenant = self.profile['tenant_id']
    params = {'tenant': tenant, 'title': keyword, 'order': SORTS.get(sort, ''), 'page': page, 'per_page': per_page}
    if term:
      terms = self.get_json('/vlabpassportapi/v1/course/term-list')['params']['list']
      params['term'] = named(terms, 'term_name', term)
    if college:
      colleges = self.get_json('/vlabpassportapi/v1/user/structures')['params']['data']
      params['struc_ids[]'] = named(colleges, 'display_name', college)
    data = self.get('/courseapi/v3/multi-search/get-course-list', {'tenant-id': str(tenant)}, **params)['data']
    courses = [
      {
        'id': str(row['id']),
        'title': html.unescape(row['title']),
        'teacher': row['realname'] or '',
        'term': row['term_name'],
        'college': row['kkxy_name'] or '',
      }
      for row in data['list']
    ]
    return int(data['total']), courses

  def course(self, course_id):
    """Course details with its replays in class order."""
    data = self.get('/courseapi/v3/multi-search/get-course-detail', course_id=course_id)['data']

    def replays(node):  # sub_list nests year -> month -> week -> [lecture]
      if isinstance(node, dict):
        node = node.values()
      for item in node:
        if isinstance(item, dict) and 'sub_title' in item:
          if item['playback_status'] == '1':
            yield item
        else:
          yield from replays(item)

    rows = sorted(replays(data['sub_list']), key=lambda row: int(row['class_begin']))
    return {
      'id': course_id,
      'title': data['title'],
      'teacher': data['realname'],
      'term': data['term_name'],
      'replays': [{'sub_id': row['id'], 'title': row['sub_title']} for row in rows],
    }

  def lecture(self, course_id, sub_id):
    """Duration and playable stream URLs of one replay."""
    data = self.get('/courseapi/v3/portal-home-setting/get-sub-info', course_id=course_id, sub_id=sub_id)['data']
    videos = (data.get('video_list') or {}).values()
    ready = {str(v['type']): v['preview_url'] for v in videos if str(v['status']) == '2'}
    streams = {name: ready[kind] for name, kind in STREAMS.items() if kind in ready}
    return {'duration_s': float(data['duration']), 'streams': streams}

  def fast_transcript(self, sub_id):
    """The platform's own (start, end, text) segments of a replay."""
    body = self.get('/courseapi/v3/web-socket/search-trans-result', sub_id=sub_id, format='json')
    segments = (segment for group in body['list'] for segment in group['all_content'])
    latest = {float(s['BeginSec']): (float(s['BeginSec']), float(s['EndSec']), s['Text']) for s in segments}
    return list(latest.values())  # the platform repeats segments in revised versions

  def sign(self, url):
    """Replace the `t` query that /play/ media requires; it is bound to the path and the current second."""
    parts = urlsplit(url)
    user, now = str(self.profile['id']), str(int(time.time()))
    secret = parts.path + user + str(self.profile['tenant_id']) + str(self.profile['phone'])[::-1] + now
    digest = hashlib.md5(secret.encode()).hexdigest()
    return parts._replace(query=urlencode({'t': f'{user}-{now}-{digest}'})).geturl()


def named(rows, key, name):
  """ID of the row whose `key` equals `name`, listing the choices otherwise."""
  match = next((row['id'] for row in rows if row[key] == name), None)
  if match is None:
    raise ValueError(f'Unknown {name!r}; choose from: {", ".join(row[key] for row in rows)}')
  return match


def upgrade_to_campus():
  """Probe the campus address in the background; once it shows the site's certificate, new connections use it."""

  def probe():
    host = urlsplit(ORIGIN).hostname
    context = ssl.create_default_context(cafile=certifi.where())
    try:
      with socket.create_connection((CAMPUS_IP, 443), timeout=3) as connection:
        context.wrap_socket(connection, server_hostname=host).close()
    except OSError:
      return
    resolve = socket.getaddrinfo  # requests resolves through it; the URL host still sets SNI and certificate checks
    socket.getaddrinfo = lambda name, *args, **kwargs: resolve(CAMPUS_IP if name == host else name, *args, **kwargs)
    print(f'Campus network detected, switch to {CAMPUS_IP}.', file=sys.stderr, flush=True)

  threading.Thread(target=probe, daemon=True).start()


def describe(error):
  """Error text without query strings, which may carry signatures."""
  if isinstance(error, requests.HTTPError):
    url = urlsplit(error.response.url)
    return f'HTTP {error.response.status_code} from {url.netloc}{url.path}'
  if isinstance(error, requests.RequestException):
    return f'Network error: {type(error).__name__}'
  if isinstance(error, OSError) and error.filename:
    try:
      name = os.path.relpath(error.filename)
    except ValueError:  # on another Windows drive
      name = error.filename
    return f'{error.strerror}: {name}'
  if isinstance(error, subprocess.CalledProcessError):
    return f'ffmpeg failed with exit status {error.returncode}'
  return str(error)
