"""A lecture's images: slides extracted from the screen recording, frames at given times, sheets and 课件.pdf."""

import math
import os
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from itertools import combinations, pairwise
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from workspace import (
  VIEWS,
  clock,
  ffmpeg,
  label,
  read_index,
  read_slides,
  recording,
  replacing,
  slide_index,
  write_json,
)

SHEET_WIDTH = 1600
LABEL = 30
WORKERS = os.cpu_count() or 1


def grab(video, time, path):
  with replacing(path) as part:
    ffmpeg('-ss', f'{time:.3f}', '-i', video, '-frames:v', '1', '-q:v', '2', '-update', '1', part)


def frames(lecture, times, view='classroom'):
  """Save the frames at `times`, rounded to whole seconds, as frames/教室-00-41-20.jpg and so on."""
  video = recording(lecture, view)
  if not video.exists():
    raise ValueError(f'{label(lecture)} has no {view} recording; run `download --{view}`')
  entry = next((entry for entry in read_index(lecture.parent).get('lectures', []) if entry['id'] == lecture.name), {})
  late = [clock(time) for time in times if time >= entry.get('duration_s', math.inf)]
  if late:
    raise ValueError(f'{label(lecture)} ends at {clock(entry["duration_s"])}, before {", ".join(late)}')
  times = [round(time) for time in times]
  paths = [lecture / 'frames' / f'{VIEWS[view]}-{clock(time).replace(":", "-")}.jpg' for time in times]
  with ThreadPoolExecutor(WORKERS) as pool:
    list(pool.map(partial(grab, video), times, paths))
  return paths


def similarity(video, start, length, folder):
  """SSIM at 480x270 of each second's frame with the next one's, for `length` seconds from `start`. Only keyframes are
  decoded, which the platform's recordings have every second."""
  stats = f'ssim-{start}.log'  # a bare name in `folder`, which keeps the filtergraph free of escaping
  graph = (
    '[0:v]fps=1,scale=480:270,format=gray,split[a][b];[a]setpts=N[x];[b]trim=start_frame=1,setpts=N[y];'
    f'[x][y]ssim=stats_file={stats}'
  )
  # a part may begin with references to frames before it, which only quiet errors can skip over
  ffmpeg('-v', 'fatal', '-ss', str(start), '-t', str(length + 1), '-skip_frame', 'nokey', '-i', video,
         '-filter_complex', graph, '-f', 'null', '-', cwd=folder)  # fmt: skip
  lines = (Path(folder) / stats).read_text(encoding='utf-8').splitlines()
  return [float(re.search(r'All:([\d.]+)', line)[1]) for line in lines][:length]


def changes(video, duration, threshold=0.995, stable=2):
  """Spans of whole seconds that each show one slide, read from the video in parallel parts. A span begins where a
  frame differs from the one before and then holds for `stable` seconds, and ends at the last frame before the next
  span that matches the one before it, so that a transition of up to `stable` seconds is left out."""
  seconds = int(duration)
  step = max(1, math.ceil(seconds / WORKERS))
  scores = [1.0] * seconds
  with tempfile.TemporaryDirectory() as folder, ThreadPoolExecutor(WORKERS) as pool:
    starts = range(0, seconds, step)
    parts = pool.map(lambda start: similarity(video, start, min(step, seconds - start), folder), starts)
    for start, part in zip(starts, parts):
      scores[start : start + len(part)] = part
  firsts = [0] + [
    second + 1
    for second in range(seconds - stable)
    if scores[second] < threshold and min(scores[second + 1 : second + 1 + stable]) >= threshold
  ]
  spans = []
  for first, following in zip(firsts, [*firsts[1:], seconds]):
    last = following - 1
    while last > max(first, following - 1 - stable) and scores[last - 1] < threshold:
      last -= 1
    spans.append((first, last))
  return spans


def bars(gray, dark=24, slack=0.01):
  """The picture inside black bars: dark lines along opposite edges, as wide on both sides within `slack` of the
  picture's size."""
  h, w = gray.shape
  rows, cols = (np.flatnonzero(gray.max(axis=axis) > dark) for axis in (1, 0))
  if not rows.size:
    return 0, 0, w, h
  top, bottom = (rows[0], rows[-1] + 1) if abs(rows[0] + rows[-1] + 1 - h) <= slack * h else (0, h)
  left, right = (cols[0], cols[-1] + 1) if abs(cols[0] + cols[-1] + 1 - w) <= slack * w else (0, w)
  return int(left), int(top), int(right), int(bottom)


def lines(counts, least, most=64):
  """The `most` lines with the most edge pixels over `least`, which bounds the search on busy pictures."""
  found = np.flatnonzero(counts > least)
  return np.sort(found[np.argsort(counts[found])[-most:]])


def canvas(gray, ratios=(16 / 9, 4 / 3), sizes=(0.4, 0.85), cover=0.97, slack=3):
  """The slide canvas of an editing view: the largest 16:9 or 4:3 rectangle with a height within `sizes` of the
  picture's, whose sides lie on edges for nearly their whole length."""
  h = len(gray)
  across = np.abs(np.diff(gray, axis=0)) > 10  # edges between a row and the next
  down = np.abs(np.diff(gray, axis=1)) > 10
  rows = lines(across.sum(axis=1), cover * sizes[0] * h * min(ratios))
  cols = lines(down.sum(axis=0), cover * sizes[0] * h)
  across = across.cumsum(axis=1, dtype=np.int32)  # edge counts along any side by subtraction
  down = down.cumsum(axis=0, dtype=np.int32)
  best, box = 0, None
  for top in rows:
    bottoms = rows[(rows >= top + h * sizes[0]) & (rows <= top + h * sizes[1])][:, None]  # against every left column
    heights = bottoms - top
    for ratio in ratios:
      rights = cols[np.searchsorted(cols, cols + heights * ratio - slack).clip(max=len(cols) - 1)]
      widths = rights - cols
      sides = np.minimum.reduce([
        (across[top, rights] - across[top, cols]) / widths.clip(min=1),
        (across[bottoms, rights] - across[bottoms, cols]) / widths.clip(min=1),
        (down[bottoms, cols] - down[top, cols]) / heights,
        (down[bottoms, rights] - down[top, rights]) / heights,
      ])  # fmt: skip
      areas = np.where((np.abs(widths - heights * ratio) <= slack) & (sides >= cover), widths * heights, 0)
      if areas.size and areas.max() > best:
        i, j = np.unravel_index(areas.argmax(), areas.shape)
        best, box = areas[i, j], (int(cols[j]) + 1, int(top) + 1, int(rights[i, j]) + 1, int(bottoms[i, 0]) + 1)
  return box


def bounds(image):
  """The picture inside any black bars, and the slide canvas within it when it shows a slide editor."""
  gray = np.asarray(image.convert('L'), dtype=np.int16)
  left, top, right, bottom = outer = bars(gray)
  inner = canvas(gray[top:bottom, left:right])
  return outer, inner and (left + inner[0], top + inner[1], left + inner[2], top + inner[3])


def extract(video, lecture, duration, repeats=3, slack=3):
  """Replace slides/ with the slides shown on a screen recording, each as it last looks before the next change, in
  001.jpg and so on, with the time it appears in index.json. Each is cropped to the picture inside any black bars, or
  to the slide canvas of an editing view when the lecture returns to that canvas in `repeats` separate stretches, which
  tells the editor's fixed layout from a framed table on a slide."""
  folder = lecture / 'slides'
  shutil.rmtree(folder, ignore_errors=True)
  spans = changes(video, duration)
  entries = [{'file': f'{n:03}.jpg', 'time_s': float(first)} for n, (first, _) in enumerate(spans, 1)]
  stills = [folder / f'{n:03}.png' for n in range(1, len(spans) + 1)]  # lossless until cropped

  def measure(still, span):
    grab(video, span[1], still)
    with Image.open(still) as image:
      return bounds(image)

  def save(still, box, entry):
    with Image.open(still) as image:
      image.crop(box).save(folder / entry['file'], quality=95)
    still.unlink()

  def near(box, other):
    return other is not None and all(abs(a - b) <= slack for a, b in zip(box, other))

  with ThreadPoolExecutor(WORKERS) as pool:
    found = list(pool.map(measure, stills, spans))
    canvases = [inner for _, inner in found]
    stretches = [box for box, before in zip(canvases, [None, *canvases]) if box and not near(box, before)]
    boxes = []
    for outer, inner in found:
      matches = [box for box in stretches if inner and near(inner, box)]
      boxes.append(matches[0] if len(matches) >= repeats else outer)  # one box for every slide of the canvas
    list(pool.map(save, stills, boxes, entries))
  write_json(slide_index(lecture), entries)
  return entries


def pick(lecture, numbers=()):
  """Entries of slides/index.json, or those with the given numbers in the given order."""
  found = read_slides(lecture)
  if not found:
    raise ValueError(f'{label(lecture)} has no slides; run `download --slides`')
  missing = [str(n) for n in numbers if not 1 <= n <= len(found)]
  if missing:
    raise ValueError(f'No slide {", ".join(missing)}; slides run from 1 to {len(found)}')
  return [found[n - 1] for n in numbers] or found


def sheets(lecture, entries, columns=4, rows=4):
  """Write labelled thumbnails, columns x rows to a sheet, as .cache/sheet-<first>-<last>.jpg."""
  width = SHEET_WIDTH // columns
  height = width * 9 // 16
  font = ImageFont.load_default(20)
  paths = []
  (lecture / '.cache').mkdir(exist_ok=True)
  for old in (lecture / '.cache').glob('sheet-*.jpg'):
    old.unlink()
  for start in range(0, len(entries), columns * rows):
    group = entries[start : start + columns * rows]
    sheet = Image.new('RGB', (SHEET_WIDTH, math.ceil(len(group) / columns) * (height + LABEL)), 'white')
    draw = ImageDraw.Draw(sheet)
    for n, entry in enumerate(group):
      x, y = n % columns * width, n // columns * (height + LABEL)
      with Image.open(lecture / 'slides' / entry['file']) as image:
        image.thumbnail((width - 8, height))
        sheet.paste(image.convert('RGB'), (x + 4, y + LABEL))
      caption = f'{entry["file"].removesuffix(".jpg")}  {clock(entry["time_s"])}'
      draw.text((x + 6, y + 5), caption, fill='black', font=font)
    first, last = (entry['file'].removesuffix('.jpg') for entry in (group[0], group[-1]))
    path = lecture / '.cache' / f'sheet-{first}-{last}.jpg'
    sheet.save(path, quality=85)
    paths.append(path)
  return paths


def fingerprint(path, size=16):
  """A 256-bit difference hash: whether each pixel of a small grayscale copy is brighter than its right neighbour."""
  with Image.open(path) as image:
    image.draft('L', (size * 8, size * 8))  # decode a JPEG at reduced scale
    pixels = image.convert('L').resize((size + 1, size)).tobytes()
  rows = (pixels[start : start + size + 1] for start in range(0, len(pixels), size + 1))
  return int(''.join('1' if left > right else '0' for row in rows for left, right in pairwise(row)), 2)


def similar(lecture, entries, distance=12):
  """Groups of slides linked through fingerprints that differ in at most `distance` bits, in slide order."""
  prints = [fingerprint(lecture / 'slides' / entry['file']) for entry in entries]
  parent = list(range(len(entries)))

  def root(i):
    while parent[i] != i:
      parent[i] = parent[parent[i]]
      i = parent[i]
    return i

  for i, j in combinations(range(len(entries)), 2):
    if (prints[i] ^ prints[j]).bit_count() <= distance:
      parent[root(j)] = root(i)
  groups = {}
  for i, entry in enumerate(entries):
    groups.setdefault(root(i), []).append(entry)
  return [group for group in groups.values() if len(group) > 1]


def write_pdf(lecture, entries):
  import img2pdf  # takes a second to load

  output = lecture / '课件.pdf'
  output.write_bytes(img2pdf.convert([str(lecture / 'slides' / entry['file']) for entry in entries]))
  for sheet in (lecture / '.cache').glob('sheet-*.jpg'):
    sheet.unlink()
  return output
