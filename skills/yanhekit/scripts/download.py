"""Download lecture resources into the course workspace."""

import json
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from urllib.parse import urlsplit

import m3u8
import requests
from imageio_ffmpeg import get_ffmpeg_exe
from requests.adapters import HTTPAdapter
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from client import ORIGIN, USER_AGENT
from slides import pdf as write_pdf
from transcript import read_captures, read_tsv, recording, recordings, replacing, write_tsv

BLOCK = 8 << 20


class NoRecording(ValueError):
  def __init__(self, lecture, view):
    super().__init__(f'{lecture}: no {view} recording')
    self.view = view


def lecture_id(title):
  """2026-10-8第6-7节 -> 10月08日第6-7节"""
  _, month, day, periods = re.fullmatch(r'(\d{4})-(\d+)-(\d+)(第.+节)', title).groups()
  return f'{int(month):02}月{int(day):02}日{periods}'


def course_folder(workspace, course):
  return workspace / re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', course['title']).strip(' .')


def read_index(folder):
  path = folder / 'course.json'
  return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def write_json(path, value):
  path.parent.mkdir(parents=True, exist_ok=True)
  with replacing(path) as part:
    part.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def lectures(course, index):
  """The current replay list as course.json lectures, keeping what earlier downloads recorded."""
  known = {lecture['sub_id']: lecture for lecture in index.get('lectures', [])}
  return [
    known.get(row['sub_id'], {'id': lecture_id(row['title']), 'sub_id': row['sub_id']}) for row in course['replays']
  ]


def sync_index(course, folder):
  """Write course.json for the current replay list."""
  index = read_index(folder)
  if index.get('course_id', course['id']) != course['id']:
    raise ValueError(f'{folder} belongs to course {index["course_id"]}')
  index = {
    'course_id': course['id'],
    'title': course['title'],
    'teacher': course['teacher'],
    'term': course['term'],
    'lectures': lectures(course, index),
  }
  write_json(folder / 'course.json', index)
  return index


def transient(error):
  if isinstance(error, requests.HTTPError):
    return error.response.status_code in (403, 408, 429, 500, 502, 503, 504)  # 403: an expired signature
  return isinstance(error, (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError))


def log_retry(state):
  error = state.outcome.exception()
  reason = f'HTTP {error.response.status_code}' if isinstance(error, requests.HTTPError) else type(error).__name__
  print(
    f'{state.fn.__name__}: {reason}, retry {state.attempt_number}/3 in {state.upcoming_sleep:.0f}s', file=sys.stderr
  )


# Each attempt re-sends the request, so /play/ URLs get a fresh signature.
persistent = retry(
  retry=retry_if_exception(transient),
  stop=stop_after_attempt(4),
  wait=wait_exponential(2, max=30),
  before_sleep=log_retry,
  reraise=True,
)


class MediaSession(requests.Session):
  """Signs /play/ requests. `check` runs before each request and between chunks; it raises to stop a download."""

  def __init__(self, client, connections, check=None):
    super().__init__()
    self.client = client
    self.check = check or (lambda: None)
    self.cookies = client.session.cookies
    self.headers.update({'User-Agent': USER_AGENT, 'Referer': ORIGIN + '/', 'Accept-Encoding': 'identity'})
    self.mount(ORIGIN, HTTPAdapter(pool_maxsize=connections))

  def request(self, method, url, *args, **kwargs):
    self.check()
    if urlsplit(url).path.startswith('/play/'):
      url = self.client.sign(url)
    return super().request(method, url, *args, **kwargs)


class Meter:
  """Thread-safe progress over `total` pieces, reported at most once a second as (fraction, bytes/s, seconds left)."""

  def __init__(self, total, done, report):
    self.total, self.done, self.report = total, done, report
    self.fetched = self.size = 0
    self.start = self.shown = time.monotonic()
    self.lock = threading.Lock()

  def add(self, size, pieces=0):
    with self.lock:
      self.size += size
      self.done += pieces
      self.fetched += pieces
      now = time.monotonic()
      if now - self.shown >= 1:
        elapsed, self.shown = now - self.start, now
        remaining = (self.total - self.done) * elapsed / self.fetched if self.fetched else None
        self.report(self.done / self.total, self.size / elapsed, remaining)


def pieces(path):
  """Where a recording's finished pieces wait, so that a stopped download resumes."""
  return path.parent / '.cache' / f'{path.stem}.pieces'


@persistent
def fetch(session, meter, url, path, headers=None):
  with session.get(url, headers=headers, stream=True, timeout=60) as response:
    response.raise_for_status()
    with replacing(path) as part, part.open('wb') as file:
      for chunk in response.iter_content(1 << 18):
        session.check()
        file.write(chunk)
        meter.add(len(chunk))


def fetch_all(session, jobs, connections, report):
  """Fetch (url, path, headers) jobs in parallel, skipping paths that already exist."""
  missing = [job for job in jobs if not job[1].exists()]
  meter = Meter(len(jobs), len(jobs) - len(missing), report)

  def run(job):
    fetch(session, meter, *job)
    meter.add(0, 1)

  pool = ThreadPoolExecutor(connections)
  try:
    list(pool.map(run, missing))
  finally:
    pool.shutdown(cancel_futures=True)  # after a failure or an interrupt, start no further pieces


@persistent
def fetch_text(session, url):
  response = session.get(url, timeout=60)
  response.raise_for_status()
  return response.text


@persistent
def content_length(session, url):
  with session.get(url, headers={'Range': 'bytes=0-0'}, stream=True, timeout=60) as response:
    response.raise_for_status()
    return int(response.headers['Content-Range'].rpartition('/')[2])


def download_video(session, url, path, connections, report):
  """Fetch a recording as pieces, 8 MiB ranges of an MP4 or the segments of an HLS stream, then join them."""
  folder = pieces(path)
  folder.mkdir(parents=True, exist_ok=True)
  with replacing(path) as joined:
    if urlsplit(url).path.endswith('.mp4'):
      size = content_length(session, url)
      spans = [(first, min(first + BLOCK, size) - 1) for first in range(0, size, BLOCK)]
      jobs = [(url, folder / f'{first:012}', {'Range': f'bytes={first}-{last}'}) for first, last in spans]
      fetch_all(session, jobs, connections, report)
      with joined.open('wb') as file:
        for _, piece, _ in jobs:
          file.write(piece.read_bytes())
    else:
      playlist = m3u8.loads(fetch_text(session, url), uri=url)
      jobs = [(segment.absolute_uri, folder / f'{n:05}.ts', None) for n, segment in enumerate(playlist.segments)]
      fetch_all(session, jobs, connections, report)
      for segment, (_, piece, _) in zip(playlist.segments, jobs):
        segment.uri = piece.name
      playlist.dump(folder / 'index.m3u8')
      subprocess.run(
        [get_ffmpeg_exe(), '-v', 'error', '-y', '-i', folder / 'index.m3u8', '-c', 'copy', joined], check=True
      )
  shutil.rmtree(folder)


def download_slides(session, slides, folder, connections, report):
  """Replace the captures with 001.jpg, 002.jpg and so on, and their times in index.json."""
  shutil.rmtree(folder, ignore_errors=True)
  folder.mkdir(parents=True)
  entries = [{'file': f'{n:03}.jpg', 'time_s': time_s} for n, (time_s, _) in enumerate(slides, 1)]
  jobs = [(url, folder / entry['file'], None) for entry, (_, url) in zip(entries, slides)]
  fetch_all(session, jobs, connections, report)
  write_json(folder / 'index.json', entries)


def download(client, course, workspace, sub_ids, *, videos=(), subtitles=False, slides=False, pdf=False,
             connections=16, overwrite=False, report, check=None):  # fmt: skip
  """Fetch the selected resources of each lecture, recording them in course.json as they finish.

  `pdf` combines every saved capture.
  `report(lecture, task, fraction=0, speed=None, remaining=None)` receives progress; task is subtitles, slides,
  a view (teacher or screen), pdf or done. `check()` may raise to stop the download; a recording's finished pieces are kept.
  """
  if not (videos or subtitles or slides or pdf):
    raise ValueError('Nothing to download; choose --subtitles, --slides or --video')
  folder = course_folder(workspace, course)
  index = sync_index(course, folder)
  session = MediaSession(client, connections, check)
  for lecture in [lecture for lecture in index['lectures'] if lecture['sub_id'] in sub_ids]:
    name, base = lecture['id'], folder / lecture['id']
    base.mkdir(parents=True, exist_ok=True)
    if videos or 'duration_s' not in lecture:
      info = client.lecture(course['id'], lecture['sub_id'])
      lecture['duration_s'] = info['duration_s']
    try:
      if subtitles:
        report(lecture, 'subtitles')
        path = base / '.cache' / 'subtitles.tsv'
        if overwrite or not path.exists():
          write_tsv(path, client.subtitles(lecture['sub_id']))
        lecture['subtitles'] = len(read_tsv(path))

      if slides:
        report(lecture, 'slides')
        if overwrite or not (base / 'slides' / 'index.json').exists():
          captures = client.slides(course['id'], lecture['sub_id'])
          download_slides(session, captures, base / 'slides', connections, partial(report, lecture, 'slides'))
        lecture['slides'] = len(read_captures(base))

      for view in videos:
        report(lecture, view)
        path = recording(base, view)
        if overwrite or not path.exists():
          url = info['streams'].get(view)
          if not url:
            raise NoRecording(name, view)
          download_video(session, url, path, connections, partial(report, lecture, view))
      if pdf and lecture.get('slides'):
        report(lecture, 'pdf')
        write_pdf(base, read_captures(base))
    finally:
      if videos:
        lecture['videos'] = list(recordings(base))
      write_json(folder / 'course.json', index)  # what finished before a failure is recorded too
    report(lecture, 'done', 1)
  return folder
