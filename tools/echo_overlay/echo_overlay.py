"""WuWa echo tuning overlay.

Sits on top of Wuthering Waves, reads the echo you are tuning from the screen, and says whether
to keep tuning it. You do the tuning yourself; nothing is clicked or sent to the game.

Reads two screens (16:9, English or Chinese client):
  - Echo Upgrade screen: the revealed substats.
  - Bag > Echoes: the selected echo's substats and "Equipped by <name>", which picks the
    character automatically when "Follow equipped" is on.

The call: an echo is scored 0-100 against the character's substat weights (roster.json). The bar
is the score only 5% of fresh echoes reach for that character. Each step compares this echo's
odds of finishing at or above the bar with a fresh echo's odds (5%):
  KEEP GOING  this echo's odds >= 1.5x a fresh echo's
  STOP        this echo's odds <= 0.67x a fresh echo's
  TOSS-UP     in between
Roll tables: Kuro Games' published probability disclosure (product information page).
"""
import ctypes
import json
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from difflib import SequenceMatcher, get_close_matches

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROSTER_FILE = os.path.join(HERE, 'roster.json')
SETTINGS_FILE = os.path.join(HERE, 'overlay_settings.json')
SNAPSHOT_DIR = os.path.join(HERE, 'snapshots')

# ---------------------------------------------------------------- roll tables

P_BELL8 = [6.7961, 7.7670, 20.3883, 24.2718, 17.4757, 14.5631, 5.8252, 2.9126]
P_CRIT8 = [23.3333, 23.3333, 23.3333, 8.0000, 8.0000, 8.0000, 3.0000, 3.0000]
V_PCT8 = [6.4, 7.1, 7.9, 8.6, 9.4, 10.1, 10.9, 11.6]

SUBSTATS = {
    'crit_rate': ([6.3, 6.9, 7.5, 8.1, 8.7, 9.3, 9.9, 10.5], P_CRIT8),
    'crit_dmg': ([12.6, 13.8, 15.0, 16.2, 17.4, 18.6, 19.8, 21.0], P_CRIT8),
    'atk_pct': (V_PCT8, P_BELL8),
    'hp_pct': (V_PCT8, P_BELL8),
    'def_pct': ([8.1, 9.0, 10.0, 10.9, 11.8, 12.8, 13.8, 14.7], P_BELL8),
    'energy_regen': ([6.8, 7.6, 8.4, 9.2, 10.0, 10.8, 11.6, 12.4], P_BELL8),
    'basic_dmg': (V_PCT8, P_BELL8),
    'heavy_dmg': (V_PCT8, P_BELL8),
    'skill_dmg': (V_PCT8, P_BELL8),
    'liberation_dmg': (V_PCT8, P_BELL8),
    'atk_flat': ([30, 40, 50, 60], [6.7961, 52.4272, 37.8641, 2.9126]),
    'def_flat': ([40, 50, 60, 70], [14.5631, 44.6602, 32.0388, 8.7379]),
    'hp_flat': ([320, 360, 390, 430, 470, 510, 540, 580], P_BELL8),
}
LABEL = {
    'crit_rate': 'Crit Rate', 'crit_dmg': 'Crit DMG', 'atk_pct': 'ATK%', 'hp_pct': 'HP%', 'def_pct': 'DEF%',
    'energy_regen': 'Energy Regen', 'basic_dmg': 'Basic DMG', 'heavy_dmg': 'Heavy DMG', 'skill_dmg': 'Skill DMG',
    'liberation_dmg': 'Liberation DMG', 'atk_flat': 'ATK', 'def_flat': 'DEF', 'hp_flat': 'HP',
}
SUBSTAT_IDS = list(SUBSTATS)
MAX_SLOTS = 5
TOP_PERCENTILE = 95
KEEP_RATIO, TRASH_RATIO = 1.5, 0.67

_n = len(SUBSTAT_IDS)
_VALS = np.zeros((_n, 8))
_CUM = np.ones((_n, 8))
_MIN = {'range': np.zeros(_n), 'max': np.zeros(_n)}
_SPAN = {'range': np.zeros(_n), 'max': np.zeros(_n)}
for _i, _sid in enumerate(SUBSTAT_IDS):
    _v, _p = SUBSTATS[_sid]
    _p = np.asarray(_p, dtype=float)
    _VALS[_i, :len(_v)] = _v
    _VALS[_i, len(_v):] = _v[-1]
    _CUM[_i, :len(_v)] = np.cumsum(_p) / _p.sum()
    _MIN['range'][_i] = _v[0]
    _SPAN['range'][_i] = _v[-1] - _v[0]
    _SPAN['max'][_i] = _v[-1]


def fraction_of(stat_id, value, norm):
    """How much of a max roll this value is worth. 'max': value/max. 'range': (value-min)/(max-min)."""
    i = SUBSTAT_IDS.index(stat_id)
    if _SPAN[norm][i] == 0:
        return 0.0
    return float(min(1.0, max(0.0, (value - _MIN[norm][i]) / _SPAN[norm][i])))


def nearest_value(stat_id, value):
    return min(SUBSTATS[stat_id][0], key=lambda v: abs(v - value))


def roll_step(stat_id):
    values = SUBSTATS[stat_id][0]
    return min(b - a for a, b in zip(values, values[1:]))


def roll_grade(stat_id, value):
    values = SUBSTATS[stat_id][0]
    return values.index(nearest_value(stat_id, value)) + 1, len(values)


def _simulate(w, fixed, n, rng, norm):
    mn, span = _MIN[norm], _SPAN[norm]
    fixed_ids = [t for t, _ in fixed]
    fixed_raw = sum(w[SUBSTAT_IDS.index(t)] * fraction_of(t, v, norm) for t, v in fixed)
    avail = np.array([i for i, s in enumerate(SUBSTAT_IDS) if s not in fixed_ids])
    rem = MAX_SLOTS - len(fixed)
    raw = np.full(n, fixed_raw, dtype=float)
    if rem > 0:
        picks = avail[np.argsort(rng.random((n, len(avail))), axis=1)[:, :rem]]
        idx = (rng.random((n, rem))[..., None] > _CUM[picks]).sum(axis=-1).clip(max=7)
        vals = _VALS[picks, idx]
        safe = np.where(span[picks] > 0, span[picks], 1)
        raw += (w[picks] * np.where(span[picks] > 0, (vals - mn[picks]) / safe, 0)).sum(axis=1)
    top = np.sort(w)[::-1][:MAX_SLOTS].sum()
    return raw / (top if top > 0 else 1.0) * 100


class EchoOdds:
    def __init__(self, weights, norm='max', samples=20000, seed=7):
        self.w = np.array([float(weights.get(s, 0) or 0) for s in SUBSTAT_IDS])
        self.weights = weights
        self.norm, self.samples, self.seed = norm, samples, seed
        fresh = _simulate(self.w, [], samples, np.random.default_rng(seed), norm)
        self.target = float(np.sort(fresh)[int(np.floor(TOP_PERCENTILE / 100 * (len(fresh) - 1)))])
        self.p_fresh = float(np.mean(fresh >= self.target))

    def judge(self, substats):
        top = np.sort(self.w)[::-1][:MAX_SLOTS].sum() or 1.0
        progress = sum(self.w[SUBSTAT_IDS.index(t)] * fraction_of(t, v, self.norm) for t, v in substats) / top * 100
        complete = len(substats) >= MAX_SLOTS
        if complete:
            p_cont = 1.0 if progress >= self.target else 0.0
            verdict = 'keeper' if progress >= self.target else 'done_low'
        else:
            cont = _simulate(self.w, substats, self.samples, np.random.default_rng(self.seed + 1), self.norm)
            p_cont = float(np.mean(cont >= self.target))
            ratio = p_cont / (self.p_fresh or 1e-4)
            verdict = 'keep' if ratio >= KEEP_RATIO else 'stop' if ratio <= TRASH_RATIO else 'tossup'
        return {'verdict': verdict, 'progress': progress, 'target': self.target, 'complete': complete,
                'p_continue': p_cont * 100, 'p_fresh': self.p_fresh * 100}


def weight_class(weight):
    return 'core' if weight >= 1.2 else 'useful' if weight >= 0.5 else 'minor' if weight >= 0.2 else 'dead'


# ---------------------------------------------------------------- reading the screen

@dataclass
class TextBox:
    name: str
    x: float
    y: float
    width: float
    height: float

    @property
    def cy(self):
        return self.y + self.height / 2


NUMBER = re.compile(r"^[\d.%％ ]+$")
PLACEHOLDER = re.compile(r"upgrade|activate|substat|强化|激活|辅音|调谐", re.I)
EN_NAMES = {
    'crit_rate': 'crit rate', 'crit_dmg': 'crit dmg', 'atk_pct': 'atk', 'atk_flat': 'atk', 'hp_pct': 'hp',
    'hp_flat': 'hp', 'def_pct': 'def', 'def_flat': 'def', 'energy_regen': 'energy regen',
    'basic_dmg': 'basic attack dmg bonus', 'heavy_dmg': 'heavy attack dmg bonus',
    'skill_dmg': 'resonance skill dmg bonus', 'liberation_dmg': 'resonance liberation dmg bonus',
}
ZH_KEYWORDS = [  # checked in order; the value's % sign settles flat vs percent
    ('暴击伤害', ('crit_dmg',)), ('暴击', ('crit_rate',)), ('效率', ('energy_regen',)), ('普攻', ('basic_dmg',)),
    ('重击', ('heavy_dmg',)), ('解放', ('liberation_dmg',)), ('技能', ('skill_dmg',)),
    ('攻击', ('atk_pct', 'atk_flat')), ('生命', ('hp_pct', 'hp_flat')), ('防御', ('def_pct', 'def_flat')),
]
PCT_IDS = {'crit_rate', 'crit_dmg', 'atk_pct', 'hp_pct', 'def_pct', 'energy_regen',
           'basic_dmg', 'heavy_dmg', 'skill_dmg', 'liberation_dmg'}
REGIONS = {  # fractions of the game's client area (16:9)
    'title': (0.00, 0.00, 0.30, 0.10),
    'upgrade_subs': (0.09, 0.30, 0.40, 0.53),
    'bag_panel': (0.68, 0.40, 0.99, 0.86),
}


def _number(text):
    try:
        return float(text.replace('％', '%').replace('%', '').strip())
    except ValueError:
        return None


def identify(name_text, value_text):
    """Substat id for one row, using the value to narrow the candidates and the name to pick."""
    value = _number(value_text)
    if value is None:
        raise ValueError(f'unreadable value {value_text!r}')
    pct = '%' in value_text or '％' in value_text
    allowed = []
    for stat_id in SUBSTAT_IDS:
        if (stat_id in PCT_IDS) == pct and abs(nearest_value(stat_id, value) - value) <= roll_step(stat_id) / 2:
            allowed.append(stat_id)
    if not allowed:
        raise ValueError(f'{name_text} {value_text} is not a possible roll')
    for keyword, ids in ZH_KEYWORDS:
        if keyword in name_text:
            hits = [i for i in ids if i in allowed]
            if hits:
                return hits[0]
            raise ValueError(f'{name_text} {value_text} is not a possible roll')
    letters = re.sub(r'[^a-z]', '', name_text.lower())
    if not letters:
        raise ValueError(f'no name read for {value_text}')
    score, best = max((SequenceMatcher(None, letters, re.sub(r'[^a-z]', '', EN_NAMES[i])).ratio(), i)
                      for i in allowed)
    if score < 0.5:
        raise ValueError(f'cannot tell which substat {name_text!r} {value_text!r} is')
    return best


def parse_rows(boxes, tolerance):
    """[(stat_id, value)] top to bottom. Each value anchors a row; name fragments join the row at or above them."""
    values = sorted([b for b in boxes if NUMBER.match(b.name.strip())], key=lambda b: b.cy)
    names = [b for b in boxes if not NUMBER.match(b.name.strip()) and not PLACEHOLDER.search(b.name)
             and re.search(r'[A-Za-z一-鿿]', b.name)]
    rows = {id(v): [] for v in values}
    for n in names:
        owners = [v for v in values if v.cy - tolerance <= n.cy]
        # a wrapped second line sits ~2 tolerances below its value; anything further is not part of the row
        if owners and n.cy - owners[-1].cy <= 2.6 * tolerance:
            rows[id(owners[-1])].append(n)
    out = []
    for v in values:
        parts = sorted(rows[id(v)], key=lambda b: (round(b.cy / max(tolerance, 1)), b.x))
        if not parts:
            raise ValueError(f'no name found for {v.name}')
        stat_id = identify(' '.join(p.name for p in parts), v.name)
        out.append((stat_id, nearest_value(stat_id, _number(v.name))))
    ids = [s for s, _ in out]
    if len(set(ids)) != len(ids):
        raise ValueError(f'read the same substat twice: {ids}')
    return out


def placeholder_rows(boxes, tolerance):
    ys = sorted(b.cy for b in boxes if PLACEHOLDER.search(b.name))
    rows = 0
    last = None
    for y in ys:
        if last is None or y - last > tolerance:
            rows += 1
        last = y
    return rows


class ScreenReader:
    def __init__(self):
        from onnxocr.onnx_paddleocr import ONNXPaddleOcr
        self.engine = ONNXPaddleOcr(use_angle_cls=False, use_gpu=False)

    def read(self, img, region):
        h, w = img.shape[:2]
        x0, y0, x1, y1 = int(region[0] * w), int(region[1] * h), int(region[2] * w), int(region[3] * h)
        result = self.engine.ocr(img[y0:y1, x0:x1])
        boxes = []
        for pts, (text, _conf) in (result[0] or []) if result else []:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            boxes.append(TextBox(text, min(xs) + x0, min(ys) + y0, max(xs) - min(xs), max(ys) - min(ys)))
        return boxes

    def analyze(self, img, roster_names):
        """{'screen', 'substats', 'equipped', 'error'} for one captured frame."""
        h = img.shape[0]
        tol = 0.015 * h
        title = ' '.join(b.name for b in self.read(img, REGIONS['title']))
        if re.search(r'upgrade|强化|培养', title, re.I) and not re.search(r'echoes|声骸 ?\d', title, re.I):
            boxes = self.read(img, REGIONS['upgrade_subs'])
            subs = parse_rows(boxes, tol)
            hidden = placeholder_rows(boxes, tol)
            if len(subs) + hidden != MAX_SLOTS:
                return {'screen': 'upgrade', 'error': f'read {len(subs)} substats and {hidden} locked slots'}
            return {'screen': 'upgrade', 'substats': subs, 'equipped': None}
        if re.search(r'echoes|声骸', title, re.I):
            boxes = self.read(img, REGIONS['bag_panel'])
            equipped, equipped_raw = None, None
            for b in boxes:
                if re.search(r'Equipped|装备', b.name, re.I):
                    same_row = sorted([o for o in boxes if abs(o.cy - b.cy) < tol and o.x >= b.x], key=lambda o: o.x)
                    text = ' '.join(o.name for o in same_row)
                    equipped_raw = re.sub(r'.*Equipped\s*by\s*|\s*装备中.*', '', text, flags=re.I).strip()
                    equipped = match_name(equipped_raw, roster_names)
            # the substats end at the "Echo Skill" heading (or the "Equipped by" line if the heading is hidden)
            ends = [b.cy for b in boxes if re.search(r'声骸技能|Equipped|装备', b.name, re.I)
                    or SequenceMatcher(None, re.sub(r'[^a-z]', '', b.name.lower()), 'echoskill').ratio() >= 0.65]
            cutoff = min(ends) if ends else float('inf')
            values = sorted([b for b in boxes if NUMBER.match(b.name.strip()) and b.cy < cutoff], key=lambda b: b.cy)
            if len(values) < 2:
                return {'screen': 'bag', 'error': 'no echo stats found in the right panel'}
            start = values[1].cy + tol  # the first two rows are the main stats
            rows = [b for b in boxes if start < b.cy < cutoff]
            return {'screen': 'bag', 'substats': parse_rows(rows, tol), 'equipped': equipped,
                    'equipped_raw': equipped_raw}
        return {'screen': None}


def match_name(text, names):
    by_key = {re.sub(r'[^a-z]', '', n.lower()): n for n in names}
    hit = get_close_matches(re.sub(r'[^a-z]', '', text.lower()), list(by_key), n=1, cutoff=0.75)
    return by_key[hit[0]] if hit else None


# ---------------------------------------------------------------- Windows: find and capture the game

def make_dpi_aware():
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def find_game_window():
    """(hwnd, left, top, width, height) of the game's client area, or None."""
    if sys.platform != 'win32':
        return None
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        if cls.value == 'UnrealWindow' and re.search(r'Wuthering|鸣潮|鳴潮', title.value):
            found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    if not found or user32.IsIconic(found[0]):
        return None
    hwnd = found[0]
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    point = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(point))
    if rect.right < 200 or rect.bottom < 200:
        return None
    return hwnd, point.x, point.y, rect.right, rect.bottom


def exclude_from_capture(tk_root):
    """Keep the overlay out of screen captures so it never covers what it reads (Windows 10 2004+)."""
    if sys.platform != 'win32':
        return
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetAncestor(tk_root.winfo_id(), 2)
        user32.SetWindowDisplayAffinity(hwnd, 0x11)
    except Exception:
        pass


# ---------------------------------------------------------------- settings and roster

def load_roster():
    with open(ROSTER_FILE, encoding='utf-8') as f:
        return {c['name']: c for c in json.load(f)}


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=1)
    except OSError:
        pass


# ---------------------------------------------------------------- worker

class Worker(threading.Thread):
    """Captures the game every interval, reads it, and posts results for the window."""

    def __init__(self, out_queue, interval=0.6):
        super().__init__(daemon=True)
        self.out = out_queue
        self.interval = interval
        self.snapshot_requested = threading.Event()
        self.stopped = threading.Event()
        self.roster_names = []

    def run(self):
        try:
            reader = ScreenReader()
        except Exception as e:
            self.out.put({'fatal': f'OCR failed to start: {e}'})
            return
        import mss
        with mss.mss() as grabber:
            while not self.stopped.is_set():
                started = time.time()
                try:
                    game = find_game_window()
                except Exception as e:
                    game = None
                    self.out.put({'status': f'Could not find the game window: {e}'})
                if not game:
                    self.out.put({'status': 'Game window not found'})
                else:
                    _, left, top, width, height = game
                    shot = grabber.grab({'left': left, 'top': top, 'width': width, 'height': height})
                    img = np.ascontiguousarray(np.array(shot)[:, :, :3])
                    try:
                        result = reader.analyze(img, self.roster_names)
                    except Exception as e:  # a bad read must not kill the overlay
                        result = {'screen': 'unknown', 'error': str(e)}
                    result['aspect'] = width / height
                    self.out.put(result)
                    if self.snapshot_requested.is_set():
                        self.snapshot_requested.clear()
                        self.save_snapshot(img, result)
                self.stopped.wait(max(0.05, self.interval - (time.time() - started)))

    def save_snapshot(self, img, result):
        import cv2
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        cv2.imwrite(os.path.join(SNAPSHOT_DIR, f'{stamp}.png'), img)
        with open(os.path.join(SNAPSHOT_DIR, f'{stamp}.json'), 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=1, default=str)
        self.out.put({'status': f'Snapshot saved: snapshots/{stamp}.png'})


# ---------------------------------------------------------------- overlay window

COLORS = {
    'bg': '#10151b', 'panel': '#18202a', 'ink': '#e6ebf1', 'muted': '#8d99a6',
    'keep': '#4cc38a', 'stop': '#ef6b5b', 'tossup': '#e3b341', 'neutral': '#8d99a6',
}
VERDICTS = {
    'keep': ('KEEP GOING', 'keep'),
    'stop': ('STOP - scrap it', 'stop'),
    'tossup': ('TOSS-UP - your call', 'tossup'),
    'keeper': ('KEEPER - top 5%', 'keep'),
    'done_low': ('DONE - below the top-5% bar', 'stop'),
}


class Overlay:
    def __init__(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.roster = load_roster()
        self.settings = load_settings()
        self.norm = self.settings.get('scoring', 'max')
        self.odds_cache = {}
        self.last_subs = None
        self.pending = (None, 0)
        self.q = queue.Queue()
        self.worker = Worker(self.q)
        self.worker.roster_names = list(self.roster)

        root = self.root = tk.Tk()
        root.title('WuWa Echo Odds')
        root.overrideredirect(True)
        root.attributes('-topmost', True)
        root.attributes('-alpha', 0.93)
        root.configure(bg=COLORS['bg'])

        header = tk.Frame(root, bg=COLORS['panel'])
        header.pack(fill='x')
        tk.Label(header, text='Echo odds', bg=COLORS['panel'], fg=COLORS['muted'],
                 font=('Segoe UI', 9, 'bold')).pack(side='left', padx=8, pady=3)
        tk.Button(header, text='x', command=self.close, bg=COLORS['panel'], fg=COLORS['muted'], bd=0,
                  activebackground=COLORS['panel'], font=('Segoe UI', 9)).pack(side='right', padx=6)
        tk.Button(header, text='snapshot', command=self.worker.snapshot_requested.set, bg=COLORS['panel'],
                  fg=COLORS['muted'], bd=0, activebackground=COLORS['panel'], font=('Segoe UI', 8)).pack(side='right')
        for widget in (header, *header.winfo_children()[:1]):
            widget.bind('<ButtonPress-1>', self.start_drag)
            widget.bind('<B1-Motion>', self.drag)

        body = tk.Frame(root, bg=COLORS['bg'])
        body.pack(fill='both', padx=10, pady=(6, 8))
        row = tk.Frame(body, bg=COLORS['bg'])
        row.pack(fill='x')
        names = sorted(self.roster)
        self.character = tk.StringVar(value=self.settings.get('character', names[0]))
        if self.character.get() not in self.roster:
            self.character.set(names[0])
        combo = ttk.Combobox(row, textvariable=self.character, values=names, state='readonly', width=20)
        combo.pack(side='left')
        combo.bind('<<ComboboxSelected>>', lambda _e: self.character_changed())
        self.follow = tk.BooleanVar(value=self.settings.get('follow_equipped', True))
        tk.Checkbutton(row, text='Follow equipped', variable=self.follow, command=self.persist,
                       bg=COLORS['bg'], fg=COLORS['muted'], selectcolor=COLORS['panel'],
                       activebackground=COLORS['bg'], font=('Segoe UI', 8)).pack(side='left', padx=6)

        self.verdict = tk.Label(body, text='Waiting for the game', bg=COLORS['bg'], fg=COLORS['neutral'],
                                font=('Segoe UI', 15, 'bold'), anchor='w')
        self.verdict.pack(fill='x', pady=(8, 0))
        self.detail = tk.Label(body, text='', bg=COLORS['bg'], fg=COLORS['ink'], font=('Segoe UI', 9),
                               anchor='w', justify='left', wraplength=300)
        self.detail.pack(fill='x')
        self.subs = tk.Label(body, text='', bg=COLORS['bg'], fg=COLORS['ink'], font=('Consolas', 9),
                             anchor='w', justify='left')
        self.subs.pack(fill='x', pady=(6, 0))
        self.status = tk.Label(body, text='Starting OCR...', bg=COLORS['bg'], fg=COLORS['muted'],
                               font=('Segoe UI', 8), anchor='w')
        self.status.pack(fill='x', pady=(6, 0))

        root.geometry(f"+{self.settings.get('x', 60)}+{self.settings.get('y', 140)}")
        root.after(300, lambda: exclude_from_capture(root))
        self.worker.start()
        root.after(150, self.poll)

    # window plumbing
    def start_drag(self, event):
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def drag(self, event):
        x, y = event.x_root - self._drag[0], event.y_root - self._drag[1]
        self.root.geometry(f'+{x}+{y}')
        self.settings['x'], self.settings['y'] = x, y

    def persist(self):
        self.settings.update({'character': self.character.get(), 'follow_equipped': self.follow.get(),
                              'scoring': self.norm})
        save_settings(self.settings)

    def close(self):
        self.persist()
        self.worker.stopped.set()
        self.root.destroy()

    def character_changed(self):
        self.persist()
        self.last_subs = None

    # results
    def odds(self, name):
        key = (name, self.norm)
        if key not in self.odds_cache:
            self.odds_cache[key] = EchoOdds(self.roster[name]['weights'], norm=self.norm)
        return self.odds_cache[key]

    def poll(self):
        try:
            while True:
                self.show(self.q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(150, self.poll)

    def show(self, result):
        if 'fatal' in result:
            self.verdict.config(text='Overlay stopped', fg=COLORS['stop'])
            self.status.config(text=result['fatal'])
            return
        if 'status' in result and 'screen' not in result:
            self.status.config(text=result['status'])
            return
        aspect = result.get('aspect', 16 / 9)
        warn = '' if abs(aspect - 16 / 9) < 0.03 else '  (game is not 16:9 - readings may be off)'
        screen = result.get('screen')
        if result.get('equipped') and self.follow.get() and result['equipped'] != self.character.get():
            self.character.set(result['equipped'])
            self.character_changed()
        elif result.get('equipped_raw') and not result.get('equipped'):
            warn += f'  (equipped by {result["equipped_raw"]}, not in roster.json)'
        if not screen:
            self.status.config(text='Open the Echo Upgrade screen or Bag > Echoes' + warn)
            return
        if result.get('error'):
            self.status.config(text=f'Reading {screen}: {result["error"]}' + warn)
            return
        subs = result['substats']
        key = (self.character.get(), tuple(subs))
        # only change the call once the same reading comes in twice (skips mid-animation frames)
        if key != self.pending[0]:
            self.pending = (key, 1)
            return
        self.pending = (key, self.pending[1] + 1)
        self.status.config(text=f'{"Upgrade screen" if screen == "upgrade" else "Bag"} - '
                                f'{len(subs)}/5 substats{warn}')
        if key == self.last_subs:
            return
        self.last_subs = key
        self.render(subs)

    def render(self, subs):
        name = self.character.get()
        odds = self.odds(name)
        weights = self.roster[name]['weights']
        lines = []
        for stat_id, value in subs:
            grade, of = roll_grade(stat_id, value)
            unit = '%' if stat_id in PCT_IDS else ''
            lines.append(f'{LABEL[stat_id]:<15}{value:>6g}{unit:<1}  {grade}/{of}  '
                         f'{weight_class(weights.get(stat_id, 0) or 0)}')
        self.subs.config(text='\n'.join(lines))
        if not subs:
            self.verdict.config(text='Tune to +5', fg=COLORS['neutral'])
            self.detail.config(text=f'{name}: top-5% bar is {odds.target:.0f}/100. '
                                    f'A fresh echo has {odds.p_fresh * 100:.0f}% to reach it.')
            return
        r = odds.judge(subs)
        text, color = VERDICTS[r['verdict']]
        self.verdict.config(text=text, fg=COLORS[color])
        if r['complete']:
            self.detail.config(text=f'Score {r["progress"]:.0f} vs bar {r["target"]:.0f} for {name}')
        else:
            self.detail.config(text=f'{r["p_continue"]:.1f}% to reach the bar vs {r["p_fresh"]:.1f}% '
                                    f'for a fresh echo\nScore so far {r["progress"]:.0f}, bar {r["target"]:.0f} '
                                    f'({name})')

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    make_dpi_aware()
    Overlay().run()
