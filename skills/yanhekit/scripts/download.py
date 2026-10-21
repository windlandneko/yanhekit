"""Download lecture resources into the course workspace."""

import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from urllib.parse import urlsplit

import m3u8
import requests
from requests.adapters import HTTPAdapter
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from client import FAILURES, ORIGIN, USER_AGENT, describe
from transcribe import transcribe_lecture
from workspace import (
  Ticker,
  count,
  ffmpeg,
  holdings,
  log,
  recording,
  replacing,
  select,
  slide_index,
  stamp,
  sync_index,
  write_json,
)

BLOCK = 8 << 20
CONNECTIONS = 16
WAITING = 2  # screen recordings fetched only for slides


def transient(error):
  if isinstance(error, requests.HTTPError):
    return error.response.status_code in (403, 408, 429, 500, 502, 503, 504)  # 403: an expired signature
  return isinstance(error, (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError))


def log_retry(state):
  error = state.outcome.exception()
  reason = f'HTTP {error.response.status_code}' if isinstance(error, requests.HTTPError) else type(error).__name__
  log(None, f'{state.fn.__name__}: {reason}, retry {state.attempt_number}/3 in {state.upcoming_sleep:.0f}s')


# Each attempt re-sends the request, so /play/ URLs get a fresh signature.
persistent = retry(
  retry=retry_if_exception(transient),
  stop=stop_after_attempt(4),
  wait=wait_exponential(2, max=30),
  before_sleep=log_retry,
  reraise=True,
)


class MediaSession(requests.Session):
  """Signs /play/ requests."""

  def __init__(self, client):
    super().__init__()
    self.client = client
    self.cookies = client.session.cookies
    self.headers.update({'User-Agent': USER_AGENT, 'Referer': ORIGIN + '/', 'Accept-Encoding': 'identity'})
    self.mount('https://', HTTPAdapter(pool_maxsize=CONNECTIONS))

  def request(self, method, url, *args, **kwargs):
    if urlsplit(url).path.startswith('/play/'):
      url = self.client.sign(url)
    return super().request(method, url, *args, **kwargs)


class Meter(Ticker):
  """Thread-safe progress over `total` parts, reported at each tenth and every 10 seconds with the speed and time left."""

  def __init__(self, total, done, title, say):
    self.total, self.done, self.initial, self.title = total, done, done, title
    self.size = 0
    self.start = time.monotonic()
    self.lock = threading.Lock()
    super().__init__(say)
    self.poke()

  def add(self, size, parts=0):
    with self.lock:
      before = self.done * 10 // self.total
      self.size += size
      self.done += parts
      crossed = before < self.done * 10 // self.total < 10
    if crossed:
      self.poke()

  def status(self):
    with self.lock:
      line = f'{self.title} {self.done * 100 // self.total}%'
      elapsed = time.monotonic() - self.start
      if self.size:
        line += f', {self.size / elapsed / 1e6:.0f} MB/s'
      if fetched := self.done - self.initial:
        line += f', {stamp((self.total - self.done) * elapsed / fetched)} left'
      return line


def parts(path):
  """Where a recording's finished parts wait, so that a stopped download resumes."""
  return path.parent / '.cache' / f'{path.stem}.parts'


@persistent
def fetch(session, meter, url, path, headers=None):
  with session.get(url, headers=headers, stream=True, timeout=60) as response:
    response.raise_for_status()
    with replacing(path) as part, part.open('wb') as file:
      for chunk in response.iter_content(1 << 18):
        file.write(chunk)
        meter.add(len(chunk))


def fetch_all(session, jobs, title, say):
  """Fetch (url, path, headers) jobs in parallel, skipping paths that already exist."""
  missing = [job for job in jobs if not job[1].exists()]
  if not missing:
    return
  with Meter(len(jobs), len(jobs) - len(missing), title, say) as meter:

    def run(job):
      fetch(session, meter, *job)
      meter.add(0, 1)

    pool = ThreadPoolExecutor(CONNECTIONS)
    try:
      list(pool.map(run, missing))
    finally:
      pool.shutdown(cancel_futures=True)  # after a failure or an interrupt, start no further parts


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


def download_video(session, url, path, title, say):
  """Fetch a recording as parts, 8 MiB ranges of an MP4 or the segments of an HLS stream, then join them."""
  folder = parts(path)
  folder.mkdir(parents=True, exist_ok=True)
  with replacing(path) as joined:
    if urlsplit(url).path.endswith('.mp4'):
      size = content_length(session, url)
      spans = [(first, min(first + BLOCK, size) - 1) for first in range(0, size, BLOCK)]
      jobs = [(url, folder / f'{first:012}', {'Range': f'bytes={first}-{last}'}) for first, last in spans]
      fetch_all(session, jobs, title, say)
      with Ticker(say, f'joining the parts of {path.name}'), joined.open('wb') as file:
        for _, piece, _ in jobs:
          file.write(piece.read_bytes())
    else:
      playlist = m3u8.loads(fetch_text(session, url), uri=url)
      jobs = [(segment.absolute_uri, folder / f'{n:05}.ts', None) for n, segment in enumerate(playlist.segments)]
      fetch_all(session, jobs, title, say)
      for segment, (_, piece, _) in zip(playlist.segments, jobs):
        segment.uri = piece.name
      playlist.dump(folder / 'index.m3u8')
      with Ticker(say, f'joining the segments of {path.name}'):
        ffmpeg('-i', folder / 'index.m3u8', '-c', 'copy', joined)
  shutil.rmtree(folder)
  say(f'saved {path.name}, {path.stat().st_size / 1e9:.1f} GB')


def download(client, course, folder, names=None, *, latest=None, videos=(), slides=False,
             transcribe=False, overwrite=False):  # fmt: skip
  """Fetch the selected resources of each lecture, printing its progress and recording its length in course.json;
  print what each lecture holds at the end and return how many lectures failed.

  `names` are lecture IDs, by default every lecture not dropped or the last `latest` of them; a named dropped lecture
  is unmarked. Slides and transcripts are made in two queues of their own while the next lectures download. Slides come
  from the screen recording, which is removed afterwards unless `videos` keeps it; at most WAITING such recordings are
  on disk at once.
  """
  if not (videos or slides or transcribe):
    raise ValueError('Nothing to download; choose --classroom, --screen, --slides or --transcribe')
  index = sync_index(course, folder)
  chosen = select(index, names, latest)
  if not chosen:
    raise ValueError(f'{course["title"]} has no replays to download')
  session = MediaSession(client)
  room = threading.BoundedSemaphore(WAITING)

  def make_slides(lecture, base, say, temporary):
    """Extract the slides of a downloaded lecture; return whether it failed."""
    from slides import extract  # numpy and Pillow take a quarter of a second to load

    try:
      with Ticker(say, 'extracting slides from the screen recording'):
        entries = extract(recording(base, 'screen'), base, lecture['duration_s'])
      say(count(len(entries), 'slide'))
      return False
    except FAILURES as error:
      say(f'slide extraction failed: {describe(error)}')
      return True
    finally:
      if temporary:
        if slide_index(base).exists():  # kept for another try after a failure
          recording(base, 'screen').unlink(missing_ok=True)
        room.release()

  slide_queue, transcript_queue = ThreadPoolExecutor(1), ThreadPoolExecutor(1)
  jobs, failed = [], set()  # (lecture ID, job) and the IDs of lectures that failed a step
  try:
    for lecture in chosen:
      base = folder / lecture['id']
      say = partial(log, base)
      base.mkdir(parents=True, exist_ok=True)
      lecture.pop('dropped', None)
      extracting = slides and (overwrite or not slide_index(base).exists())
      temporary = extracting and 'screen' not in videos and not recording(base, 'screen').exists()
      if temporary and not room.acquire(blocking=False):
        with Ticker(say, f'waiting for slide extraction; {WAITING} screen recordings are on disk'):
          room.acquire()
      try:
        if videos or extracting or 'duration_s' not in lecture:
          info = client.lecture(course['id'], lecture['sub_id'])
          lecture['duration_s'] = info['duration_s']
        for view in [*videos, 'screen'] if temporary else videos:
          path = recording(base, view)
          if overwrite or not path.exists():
            if view not in info['streams']:
              raise ValueError(f'no {view} recording')
            download_video(session, info['streams'][view], path, f'{view} video', say)
      except FAILURES as error:
        failed.add(lecture['id'])
        say(f'download failed: {describe(error)}')
        if temporary:
          room.release()
        continue
      finally:
        write_json(folder / 'course.json', index)  # what finished before a failure is recorded too
      transcribing = transcribe and (not temporary or recording(base, 'classroom').exists())
      if transcribe and not transcribing:
        failed.add(lecture['id'])
        say('no recording to transcribe besides the screen recording fetched for slides; add --classroom')
      work = [name for name, wanted in (('slides', extracting), ('transcript', transcribing)) if wanted]
      if work:
        say(f'queued for {" and ".join(work)}')
      if extracting:
        jobs.append((lecture['id'], slide_queue.submit(make_slides, lecture, base, say, temporary)))
      if transcribing:
        jobs.append((lecture['id'], transcript_queue.submit(transcribe_lecture, base, overwrite, say)))
    failed |= {name for name, job in jobs if job.result()}
    for lecture in chosen:
      log(folder / lecture['id'], f'holds {holdings(lecture, folder / lecture["id"])}')
    return len(failed)
  finally:
    for queue in (slide_queue, transcript_queue):
      queue.shutdown(cancel_futures=True)  # after an interrupt, start no further lectures
