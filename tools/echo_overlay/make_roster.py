"""Builds roster.json from builds_source.json.

How a guide's substat priority becomes weights (0-2):
  - Energy Regen is weighted by how much the character needs, from the guide's target:
    up to 115% -> 0.6, up to 130% -> 0.9, up to 160% -> 1.3, up to 200% -> 1.6, above -> 2.0,
    "until satisfied" with no number -> 0.8, not listed -> 0.1. The overlay can't see your total ER,
    so it can't stop valuing ER once you have enough.
  - Every other tier, in the guide's order, gets 2.0, 1.4, 1.0, 0.7, 0.5, 0.35, 0.25.
    Stats joined by '=' or '≥' share a tier. After '>>>' the remaining tiers are scaled by 0.6.
  - Flat ATK/HP/DEF never go above 0.35 (a flat roll is worth far less than its % twin).
  - Stats the guide doesn't list: DMG bonuses 0.1, Crit 0.1, other % stats 0.05, flats 0.02.
Characters in "keep" keep their hand-reviewed weights from the existing roster.json.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
TIER_WEIGHTS = [2.0, 1.4, 1.0, 0.7, 0.5, 0.35, 0.25]
FLAT_CAP = 0.35
FLATS = {'atk_flat', 'hp_flat', 'def_flat'}
DMG = {'basic_dmg', 'heavy_dmg', 'skill_dmg', 'liberation_dmg'}
ALL = ['crit_rate', 'crit_dmg', 'atk_pct', 'hp_pct', 'def_pct', 'energy_regen', 'basic_dmg', 'heavy_dmg',
       'skill_dmg', 'liberation_dmg', 'atk_flat', 'hp_flat', 'def_flat']


def stat_ids(token):
    raw = token.strip().lower()
    t = re.sub(r'\(.*?\)', '', raw).strip()  # drop "(until satisfied)" etc.
    if not t:
        return []
    if 'energy regen' in t:
        return ['energy_regen']
    if 'crit dmg' in t:
        return ['crit_dmg']
    if 'crit rate' in t:
        return ['crit_rate']
    hits = []
    for key, sid in (('basic', 'basic_dmg'), ('heavy', 'heavy_dmg'), ('liberation', 'liberation_dmg'),
                     ('skill', 'skill_dmg')):
        if key in t:
            hits.append(sid)
    if hits:
        return hits
    # "HP (Until 50000)" means total HP, so the % roll is what gets you there
    flat = 'flat' in t or ('%' not in t and 'until' not in raw)
    for key in ('atk', 'hp', 'def'):
        if re.search(rf'\b{key}\b', t):
            return [f'{key}_flat' if flat else f'{key}_pct']
    raise ValueError(f'unknown substat {token!r}')


def er_weight(er):
    """Weight from the guide's ER target, or None when the guide gives no target."""
    nums = [float(n) for n in re.findall(r'\d+(?:\.\d+)?', er or '')]
    if not nums:
        return 0.8 if 'satisf' in (er or '').lower() else None
    top = max(nums)
    return 0.6 if top <= 115 else 0.9 if top <= 130 else 1.3 if top <= 160 else 1.6 if top <= 200 else 2.0


def weights_for(c):
    w = {}
    pieces = re.split(r'(>>>|>)', c['priority'])
    tier, scale = 0, 1.0
    listed_er = False
    er_tier_weight = None
    for piece in pieces:
        if piece == '>>>':
            scale = 0.6
            continue
        if piece == '>':
            continue
        ids = []
        for token in re.split(r'=|≥', piece):
            ids += stat_ids(token)
        ids = [i for i in ids if i not in w and not (i == 'energy_regen' and listed_er)]
        if 'energy_regen' in ids:
            listed_er = True
            ids.remove('energy_regen')
            if not ids and tier > 0:  # ER ranked among the other stats rather than first
                er_tier_weight = TIER_WEIGHTS[min(tier, len(TIER_WEIGHTS) - 1)] * scale
                tier += 1
        if not ids:
            continue
        base = TIER_WEIGHTS[min(tier, len(TIER_WEIGHTS) - 1)] * scale
        for i in ids:
            w[i] = min(base, FLAT_CAP) if i in FLATS else base
        tier += 1
    target = er_weight(c['er'])
    if not listed_er:
        w['energy_regen'] = 0.1
    else:
        w['energy_regen'] = target if target is not None else (er_tier_weight or 0.8)
    for i in ALL:
        if i not in w:
            w[i] = 0.1 if (i in DMG or i.startswith('crit')) else 0.02 if i in FLATS else 0.05
    return {k: round(w[k], 3) for k in ALL}


def main():
    src = json.load(open(os.path.join(HERE, 'builds_source.json'), encoding='utf-8'))
    old = {c['name']: c for c in json.load(open(os.path.join(HERE, 'roster.json'), encoding='utf-8'))}
    out = []
    for name in src['keep']:
        c = dict(old[name])
        c['note'] = src.get('keep_notes', {}).get(name, c.get('note', 'Hand-reviewed weights'))
        c['reviewed'] = True
        out.append(c)
    for c in src['characters']:
        out.append({
            'id': re.sub(r'[^a-z0-9]+', '-', c['name'].lower()).strip('-'),
            'name': c['name'],
            'reviewed': False,
            'weights': weights_for(c),
            'note': f"{c['role']} · ER {c['er']} · {c['priority']}",
            'source': c['source'],
        })
    out.sort(key=lambda c: c['name'])
    with open(os.path.join(HERE, 'roster.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f'{len(out)} characters written')


if __name__ == '__main__':
    main()
