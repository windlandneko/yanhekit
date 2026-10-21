"""Transcribe lecture recordings with BCut, Bilibili's free speech recognition."""

import json
import time
from functools import partial

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from client import FAILURES, describe
from transcript import write_text
from workspace import Ticker, count, ffmpeg, log, recordings, replacing, timeline, write_tsv

API = 'https://member.bilibili.com/x/bcut/rubick-interface'
MODEL = 7


class Rejected(RuntimeError):
  """BCut could not transcribe the audio it was given."""


class BCut:
  """A BCut client reporting its steps and waits through a Ticker."""

  def __init__(self, ticker):
    self.ticker = ticker
    self.session = requests.Session()
    self.session.headers['User-Agent'] = 'Mozilla/5.0'
    retry = Retry(3, backoff_factor=2, status_forcelist=[500, 502, 503, 504], allowed_methods=None)
    self.session.mount('https://', HTTPAdapter(max_retries=retry))

  def call(self, method, path, **kwargs):
    for delay in (30, 60, 120, 240, None):  # 412 is Bilibili's rate limit, which lifts after minutes
      response = self.session.request(method, API + path, timeout=60, **kwargs)
      if response.status_code not in (412, 429) or delay is None:
        break
      self.ticker.say(f'BCut is rate limiting; retrying in {delay}s')
      time.sleep(delay)
    response.raise_for_status()
    body = response.json()
    if body['code']:
      raise RuntimeError(f'BCut {path}: {body.get("message") or body["code"]}')
    return body['data']

  def upload(self, audio):
    """Upload in the parts BCut asks for and return the resource URL."""
    resource = self.call(
      'POST',
      '/resource/create',
      data={
        'type': 2,
        'name': audio.name,
        'size': audio.stat().st_size,
        'resource_file_type': 'm4a',
        'model_id': MODEL,
      },
    )
    etags = []
    with audio.open('rb') as file:
      for url in resource['upload_urls']:
        response = self.session.put(url, data=file.read(resource['per_size']), timeout=120)
        response.raise_for_status()
        etags.append(response.headers['Etag'])
    done = self.call(
      'POST',
      '/resource/create/complete',
      data={
        'in_boss_key': resource['in_boss_key'],
        'resource_id': resource['resource_id'],
        'etags': ','.join(etags),
        'upload_id': resource['upload_id'],
        'model_id': MODEL,
      },
    )
    return done['download_url']

  def transcribe(self, audio, timeout=7200):
    self.ticker.set(f'uploading {audio.stat().st_size / 1e6:.0f} MB of audio to BCut')
    resource = self.upload(audio)
    task = self.call('POST', '/task', json={'resource': resource, 'model_id': str(MODEL)})['task_id']
    self.ticker.set('waiting for BCut to transcribe')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      result = self.call('GET', '/task/result', params={'model_id': MODEL, 'task_id': task})
      if result['state'] == 4:
        utterances = json.loads(result['result'])['utterances']
        return [(u['start_time'] / 1000, u['end_time'] / 1000, u['transcript']) for u in utterances]
      if result['state'] == 3:
        raise Rejected(f'BCut task failed: {result.get("remark") or "no reason given"}')
      time.sleep(10)
    raise TimeoutError(f'BCut task {task} still running after {timeout // 60} minutes')


def extract_audio(video, audio, encode=False):
  """The recording's sound track as an .m4a file: copied as it is, which takes seconds, or encoded to mono AAC."""
  codec = ['-ac', '1', '-ar', '16000', '-b:a', '48k'] if encode else ['-c:a', 'copy']
  with replacing(audio) as part:
    ffmpeg('-i', video, '-vn', *codec, part)


def transcribe_lecture(lecture, overwrite, say):
  """Transcribe the lecture's first recording into 字幕.txt, reporting through `say`; return whether it failed."""
  if timeline(lecture).exists() and not overwrite:
    say('kept existing transcript')
    return False
  video = next(iter(recordings(lecture).values()), None)
  if video is None:
    say('no recording to transcribe; run `download --classroom` first')
    return True
  try:
    audio = lecture / '.cache' / 'audio.m4a'
    with Ticker(say) as ticker:
      if overwrite or not audio.exists():
        ticker.set(f'copying the sound track of {video.name}')
        extract_audio(video, audio)
      bcut = BCut(ticker)
      try:
        rows = bcut.transcribe(audio)
      except Rejected:
        say('BCut rejected the sound track')
        ticker.set('encoding the sound track to AAC')
        extract_audio(video, audio, encode=True)
        rows = bcut.transcribe(audio)
    segments = write_tsv(timeline(lecture), rows)
    write_text(lecture)
    say(f'{count(segments, "segment")} in 字幕.txt')
    return False
  except FAILURES as error:
    say(f'transcription failed: {describe(error)}')
    return True


def transcribe(lectures, overwrite=False):
  """Transcribe the lectures one after another; return how many failed."""
  return sum(transcribe_lecture(lecture, overwrite, partial(log, lecture)) for lecture in lectures)
