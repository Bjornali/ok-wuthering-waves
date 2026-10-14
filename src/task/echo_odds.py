"""Echo keep/discard odds, ported from the Echo Diagnostics page.

Roll tables come from Kuro Games' published probability disclosure. Every substat type is equally
likely from the remaining pool (1/13, 1/12, ... as slots fill) and each type rolls from its own
value distribution. An echo is scored 0-100 against a character's substat weights; the "top bar"
is the score only 5% of fresh echoes reach for that character. Each tuning step compares the odds
of this echo finishing at or above the bar against the odds of a fresh echo doing so.

Normalisation: the page credits each roll by (value - min) / (max - min), so a minimum roll is worth
nothing. 'max' credits value / max instead, so a 6.3% Crit Rate counts as 60% of a 10.5% roll.
'range' reproduces the page exactly.
"""
import json
import os

import numpy as np

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
SUBSTAT_IDS = list(SUBSTATS)
MAX_SLOTS = 5
TOP_PERCENTILE = 95
KEEP_RATIO = 1.5
TRASH_RATIO = 0.67

# Canonical names produced by EnhanceEchoTask's OCR normalisation -> substat ids.
ZH_TO_ID = {
    '暴击': 'crit_rate',
    '暴击伤害': 'crit_dmg',
    '攻击百分比': 'atk_pct',
    '攻击': 'atk_flat',
    '生命百分比': 'hp_pct',
    '生命': 'hp_flat',
    '防御百分比': 'def_pct',
    '防御': 'def_flat',
    '共鸣效率': 'energy_regen',
    '普攻伤害加成': 'basic_dmg',
    '重击伤害加成': 'heavy_dmg',
    '共鸣技能伤害加成': 'skill_dmg',
    '共鸣解放伤害加成': 'liberation_dmg',
}

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
NORMALISATIONS = list(_MIN)


def fraction_of(stat_id, value, norm='max'):
    i = SUBSTAT_IDS.index(stat_id)
    if _SPAN[norm][i] == 0:
        return 0.0
    return float(min(1.0, max(0.0, (value - _MIN[norm][i]) / _SPAN[norm][i])))


def nearest_value(stat_id, value):
    values = SUBSTATS[stat_id][0]
    return min(values, key=lambda v: abs(v - value))


def roll_step(stat_id):
    """Smallest gap between two legal values, used to judge whether an OCR read is plausible."""
    values = SUBSTATS[stat_id][0]
    return min(b - a for a, b in zip(values, values[1:]))


def _weights_vector(weights):
    return np.array([float(weights.get(s, 0) or 0) for s in SUBSTAT_IDS])


def _max_sum(w):
    top = np.sort(w)[::-1][:MAX_SLOTS].sum()
    return top if top > 0 else 1.0


def _percentile(scores, p):
    s = np.sort(scores)
    idx = int(np.floor((p / 100) * (len(s) - 1)))
    return float(s[max(0, min(len(s) - 1, idx))])


def _simulate(w, fixed_ids, fixed_values, n, rng, norm):
    """Score n random completions with the given substats held fixed."""
    mn, span = _MIN[norm], _SPAN[norm]
    fixed_raw = sum(w[SUBSTAT_IDS.index(t)] * fraction_of(t, v, norm) for t, v in zip(fixed_ids, fixed_values))
    avail = np.array([i for i, s in enumerate(SUBSTAT_IDS) if s not in fixed_ids])
    rem = MAX_SLOTS - len(fixed_ids)
    raw = np.full(n, fixed_raw, dtype=float)
    if rem > 0:
        picks = avail[np.argsort(rng.random((n, len(avail))), axis=1)[:, :rem]]  # (n, rem) without replacement
        u = rng.random((n, rem))
        idx = (u[..., None] > _CUM[picks]).sum(axis=-1).clip(max=7)
        vals = _VALS[picks, idx]
        frac = np.where(span[picks] > 0, (vals - mn[picks]) / np.where(span[picks] > 0, span[picks], 1), 0)
        raw += (w[picks] * frac).sum(axis=1)
    return raw / _max_sum(w) * 100


class EchoOdds:
    """Keep/discard calls for one character's weights. The fresh-echo bar is computed once."""

    def __init__(self, weights, samples=20000, seed=7, norm='max'):
        if norm not in NORMALISATIONS:
            raise ValueError(f'unknown normalisation {norm}')
        self.w = _weights_vector(weights)
        self.samples = samples
        self.seed = seed
        self.norm = norm
        fresh = _simulate(self.w, [], [], samples, np.random.default_rng(seed), norm)
        self.target = _percentile(fresh, TOP_PERCENTILE)
        self.p_fresh = float(np.mean(fresh >= self.target))
        self.fresh_mean = float(np.mean(fresh))

    def score(self, substats):
        raw = sum(self.w[SUBSTAT_IDS.index(t)] * fraction_of(t, v, self.norm) for t, v in substats)
        return raw / _max_sum(self.w) * 100

    def judge(self, substats):
        """substats: list of (stat_id, value) in reveal order. Returns a dict with the verdict.

        verdict is 'keep', 'trash' or 'mixed' using the same rules as the Echo Diagnostics page.
        """
        ids = [t for t, _ in substats]
        if len(set(ids)) != len(ids):
            raise ValueError(f'duplicate substat types {ids}')
        progress = self.score(substats)
        complete = len(substats) >= MAX_SLOTS
        if complete:
            p_cont = 1.0 if progress >= self.target else 0.0
            cont_mean = progress
        else:
            rng = np.random.default_rng(self.seed + 1)
            cont = _simulate(self.w, ids, [v for _, v in substats], self.samples, rng, self.norm)
            p_cont = float(np.mean(cont >= self.target))
            cont_mean = float(np.mean(cont))
        ratio = p_cont / (self.p_fresh or 0.0001)
        if complete:
            verdict = 'keep' if progress >= self.target else 'trash'
        elif ratio >= KEEP_RATIO:
            verdict = 'keep'
        elif ratio <= TRASH_RATIO:
            verdict = 'trash'
        else:
            verdict = 'mixed'
        return {
            'verdict': verdict, 'progress': progress, 'target': self.target, 'complete': complete,
            'p_continue': p_cont * 100, 'p_fresh': self.p_fresh * 100, 'ratio': ratio,
            'continue_mean': cont_mean, 'fresh_mean': self.fresh_mean,
        }


ROSTER_FILE = os.path.join(os.path.dirname(__file__), 'echo_roster.json')


def load_roster(path=ROSTER_FILE):
    """Character weights exported from the Echo Diagnostics roster, keyed by display name."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return {c['name']: c for c in data}
