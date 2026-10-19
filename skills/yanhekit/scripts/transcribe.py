"""Transcribe lecture recordings with BCut, Bilibili's free speech recognition."""

import json
import subprocess
import time

import requests
from imageio_ffmpeg import get_ffmpeg_exe
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from client import describe
from transcript import recordings, replacing, write_tsv

API = 'https://member.bilibili.com/x/bcut/rubick-interface'
MODEL = 7


class BCut:
  def __init__(self):
    self.session = requests.Session()
    self.session.headers['User-Agent'] = 'Mozilla/5.0'
    retry = Retry(3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=None)
    self.session.mount('https://', HTTPAdapter(max_retries=retry))

  def call(self, method, path, **kwargs):
    response = self.session.request(method, API + path, timeout=60, **kwargs)
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
        'resource_file_type': 'mp3',
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
    print('  uploading audio', flush=True)
    resource = self.upload(audio)
    print('  transcribing', flush=True)
    task = self.call('POST', '/task', json={'resource': resource, 'model_id': str(MODEL)})['task_id']
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      result = self.call('GET', '/task/result', params={'model_id': MODEL, 'task_id': task})
      if result['state'] == 4:
        utterances = json.loads(result['result'])['utterances']
        rows = [(u['start_time'] / 1000, u['end_time'] / 1000, ' '.join(u['transcript'].split())) for u in utterances]
        return sorted(row for row in rows if row[2])
      if result['state'] == 3:
        raise RuntimeError(f'BCut task failed: {result.get("remark") or "no reason given"}')
      time.sleep(5)
    raise TimeoutError(f'BCut task {task} still running after {timeout // 60} minutes')


def extract_audio(video, audio):
  audio.parent.mkdir(parents=True, exist_ok=True)
  with replacing(audio) as part:
    subprocess.run(
      [get_ffmpeg_exe(), '-v', 'error', '-y', '-i', video, '-vn', '-ac', '1', '-ar', '16000', '-b:a', '48k', part],
      check=True,
    )


def videos(paths):
  """One recording a lecture, the teacher view first, from lecture folders, course folders or .mp4 paths."""
  for path in paths:
    if path.is_file():
      yield path
    else:  # a lecture folder, or a course folder of them; a missing path raises here
      for lecture in [path, *sorted(path.iterdir())]:
        yield from list(recordings(lecture).values())[:1]


def transcribe(paths, overwrite=False):
  """Transcribe each recording in turn, printing its lecture and outcome; return how many failed."""
  found = list(videos(paths))
  if not found:
    raise ValueError('No recordings found')
  bcut, failed = BCut(), 0
  for video in found:
    cache = video.parent / '.cache'
    print(video.parent.name, flush=True)
    if (cache / 'transcript.tsv').exists() and not overwrite:
      print('  kept existing transcript', flush=True)
      continue
    try:
      if overwrite or not (cache / 'audio.mp3').exists():
        print('  extracting audio', flush=True)
        extract_audio(video, cache / 'audio.mp3')
      rows = bcut.transcribe(cache / 'audio.mp3')
      write_tsv(cache / 'transcript.tsv', rows)
      print(f'  {len(rows)} segments', flush=True)
    except (requests.RequestException, OSError, RuntimeError, subprocess.CalledProcessError) as error:
      failed += 1
      print(f'  failed: {describe(error)}', flush=True)
  return failed
