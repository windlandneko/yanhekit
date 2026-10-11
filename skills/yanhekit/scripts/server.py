"""Local browser UI for sign-in and downloads; the page follows a shared state over server-sent events."""

import json
import logging
import os
import queue
import secrets
import shutil
import threading
import traceback
import webbrowser
from pathlib import Path

import requests
from flask import Flask, Response, abort, request, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server

import download
import eclass
import transcript
from auth import SignIn, SignInError, saved_password, saved_username

UI = Path(__file__).with_name('ui')  # built from ui/ by `pnpm build`
DEV = os.environ.get('YANHEKIT_DEV')  # a Vite dev server, such as http://localhost:5173, that proxies /api to 5174


class State:
  """JSON-serializable values; every update wakes the event streams."""

  def __init__(self, **values):
    self.values = values
    self.version = self.sent = 0
    self.changed = threading.Condition()

  def __getitem__(self, key):
    return self.values[key]

  def update(self, **values):
    with self.changed:
      self.values.update(values)
      self.version += 1
      self.changed.notify_all()

  def events(self):
    version = None
    while True:
      with self.changed:
        fresh = self.changed.wait_for(lambda seen=version: self.version != seen, timeout=15)
        version = self.version
        data = json.dumps(self.values, ensure_ascii=False) if fresh else None
      yield f'data: {data}\n\n' if fresh else ': keep-alive\n\n'  # a write also notices a closed tab
      with self.changed:  # the server asks for the next event once this one is written
        self.sent = max(self.sent, version)
        self.changed.notify_all()

  def flush(self, timeout=5):
    """Wait until a page has received the current values."""
    with self.changed:
      self.changed.wait_for(lambda: self.sent >= self.version, timeout)


def describe(error):
  """Chinese for the page, without URLs, tokens or account data; the terminal gets the traceback."""
  if isinstance(error, SignInError):
    return str(error)
  traceback.print_exception(error)
  if isinstance(error, download.NoRecording):
    return f'没有{transcript.VIEWS[error.view]}视角的录像'
  if isinstance(error, requests.HTTPError):
    return f'服务器出错（HTTP {error.response.status_code}），请稍后重试'
  if isinstance(error, requests.RequestException):
    return '网络连接失败，请稍后重试'
  return '出错了，详情见终端'


class Stop(Exception):
  """Raised inside a download that the page paused or cancelled."""


class Downloads:
  """Lectures download one at a time; the page can pause, resume, retry or cancel each one.

  `jobs` maps sub_id to what the page shows: status (queued, running, paused or failed), task, fraction, speed,
  remaining and error. A lecture leaves it when it finishes or is cancelled.
  """

  def __init__(self, client, workspace, publish, refresh):
    self.client, self.workspace = client, workspace
    self.publish, self.refresh = publish, refresh
    self.jobs = {}
    self.orders = {}  # sub_id -> (course, options, lecture folder)
    self.stop = {}  # sub_id -> pause or cancel, asked of the running lecture
    self.lock = threading.Lock()
    self.queue = queue.SimpleQueue()
    threading.Thread(target=self.work, daemon=True).start()

  def changed(self):
    self.publish({sub_id: dict(job) for sub_id, job in self.jobs.items()})

  def add(self, course, sub_ids, options):
    names = {row['sub_id']: download.lecture_id(row['title']) for row in course['replays']}
    folder = download.course_folder(self.workspace, course)
    with self.lock:
      for sub_id in sub_ids:
        if sub_id not in self.jobs:
          self.orders[sub_id] = (course, options, folder / names[sub_id])
          self.jobs[sub_id] = {'status': 'queued'}
          self.queue.put(sub_id)
      self.changed()

  def control(self, sub_id, action):
    with self.lock:
      job = self.jobs.get(sub_id)
      if job is None:
        return
      if job['status'] == 'running':
        if action == 'resume':
          self.stop.pop(sub_id, None)
        else:
          self.stop[sub_id] = action
      elif action == 'cancel':
        self.discard(sub_id)
      elif action == 'pause' and job['status'] == 'queued':
        job['status'] = 'paused'
      elif action == 'resume' and job['status'] in ('paused', 'failed'):
        job.update(status='queued', error=None)
        self.queue.put(sub_id)
      self.changed()

  def discard(self, sub_id):
    """Drop a cancelled lecture and the pieces its recording left behind."""
    del self.jobs[sub_id]
    _, options, lecture = self.orders.pop(sub_id)
    for view in options['videos']:
      shutil.rmtree(download.pieces(transcript.recording(lecture, view)), ignore_errors=True)

  def work(self):
    while True:
      sub_id = self.queue.get()
      with self.lock:
        if self.jobs.get(sub_id, {}).get('status') != 'queued':
          continue  # paused or cancelled while waiting
        self.jobs[sub_id] = {'status': 'running'}
        self.changed()
      self.run(sub_id)

  def run(self, sub_id):
    course, options, _ = self.orders[sub_id]
    stopped = error = None

    def check():
      if sub_id in self.stop:
        raise Stop

    def report(_, task, fraction=0, speed=None, remaining=None):
      with self.lock:
        self.jobs[sub_id].update(task=task, fraction=fraction, speed=speed, remaining=remaining)
        self.changed()

    try:
      download.download(self.client, course, self.workspace, [sub_id], **options, report=report, check=check)
    except Stop:
      stopped = True
    except Exception as failure:  # noqa: BLE001 - shown on the page; details stay in the terminal
      error = describe(failure)
    with self.lock:
      action = self.stop.pop(sub_id, None)
      job = self.jobs[sub_id]
      if action == 'cancel':
        self.discard(sub_id)
      elif stopped and action == 'pause':
        job.update(status='paused', speed=None, remaining=None)
      elif stopped:  # resumed before the pause took effect
        job.update(status='queued')
        self.queue.put(sub_id)
      elif error:
        job.update(status='failed', error=error, speed=None, remaining=None)
      else:
        del self.jobs[sub_id]
        del self.orders[sub_id]
      self.changed()
    self.refresh(course)


def serve(client, workspace=None, course_id=None):
  """Without a workspace, return once signed in; otherwise keep the download page open until interrupted."""
  username = saved_username()
  state = State(
    stage='checking',  # checking, credentials, verify or done
    username=username,
    saved=bool(saved_password(username)),
    captcha=None,
    phone='',
    sending=False,
    resend_at=0,
    downloads=workspace is not None,
    courses=[],
    course=None,
    jobs={},
    message='',
  )
  signin = SignIn(client, state.update)
  signed_in = threading.Event()
  step_lock = threading.Lock()

  def step(action):
    """Run one sign-in step at a time; finish once it signs in."""
    if not step_lock.acquire(blocking=False):
      raise SignInError('请稍候')
    try:
      if action():
        finish()
    finally:
      step_lock.release()

  def finish():
    signed_in.set()
    if workspace is not None:
      state.update(courses=client.courses())
      if course_id:
        show_course(client.course(course_id))
    state.update(stage='done', captcha=None)
    if getattr(client, '_capture_eclass', False):
      eclass.capture()

  def show_course(course):
    """Publish the replay list merged with what course.json records as downloaded."""
    folder = download.course_folder(workspace, course)
    lectures = download.lectures(course, download.read_index(folder))
    summary = {key: course[key] for key in ('id', 'title', 'teacher', 'term')}
    state.update(course={**summary, 'folder': str(folder.resolve()), 'lectures': lectures})

  def start():
    try:
      step(signin.start)
    except Exception as error:  # noqa: BLE001 - shown on the page; details stay in the terminal
      state.update(message=describe(error))

  def refresh(course):
    if state['course'] and state['course']['id'] == course['id']:
      show_course(course)

  if workspace is not None:
    downloads = Downloads(client, workspace, lambda jobs: state.update(jobs=jobs), refresh)

  start()  # a saved session or password signs in before any page opens
  if signed_in.is_set() and workspace is None:
    return

  app = Flask(__name__, static_folder=None)
  token = secrets.token_urlsafe(32)

  @app.before_request
  def guard():
    if request.host != f'127.0.0.1:{server.port}':  # refuse DNS rebinding
      abort(403)
    if request.path.startswith('/api/'):
      supplied = request.headers.get('X-Token') or request.args.get('token', '')
      if not secrets.compare_digest(supplied, token):
        abort(403)

  @app.get('/', defaults={'name': 'index.html'})
  @app.get('/<path:name>')
  def static(name):
    return send_from_directory(UI, name, max_age=0)

  @app.get('/api/events')
  def events():
    return Response(state.events(), mimetype='text/event-stream', headers={'Cache-Control': 'no-store'})

  @app.errorhandler(Exception)
  def failed(error):
    return error if isinstance(error, HTTPException) else ({'error': describe(error)}, 400)

  @app.post('/api/login')
  def login():
    data = request.json
    step(lambda: signin.password(data['username'], data['password'], data['captcha'], data['remember']))
    return {}

  @app.post('/api/resend')
  def resend():
    step(signin.send_code)
    return {}

  @app.post('/api/verify')
  def verify():
    step(lambda: signin.verify(request.json['code']))
    return {}

  @app.post('/api/cancel')
  def cancel():
    signin.reset()
    return {}

  @app.post('/api/course')
  def course():
    if not signed_in.is_set():
      abort(403)
    show_course(client.course(request.json['course_id']))
    return {}

  @app.post('/api/download')
  def start_download():
    if not signed_in.is_set() or workspace is None:
      abort(403)
    data = request.json
    course = client.course(data['course_id'])
    options = ('videos', 'subtitles', 'slides', 'connections', 'overwrite', 'pdf')
    downloads.add(course, data['sub_ids'], {key: data[key] for key in options})
    return {}

  @app.post('/api/job')
  def job():
    if workspace is None or request.json['action'] not in ('pause', 'resume', 'cancel'):
      abort(400)
    downloads.control(request.json['sub_id'], request.json['action'])
    return {}

  logging.getLogger('werkzeug').setLevel(logging.WARNING)
  server = make_server('127.0.0.1', 5174 if DEV else 0, app, threaded=True)
  threading.Thread(target=server.serve_forever, daemon=True).start()
  page = DEV or f'http://127.0.0.1:{server.port}'
  url = f'{page}/#{token}'
  webbrowser.open(url)
  print(f'{"Download" if workspace else "Sign-in"} page: {url}', flush=True)  # for when no browser opens
  try:
    if workspace is None:
      signed_in.wait()
      state.flush()
    else:
      threading.Event().wait()
  finally:
    server.shutdown()
