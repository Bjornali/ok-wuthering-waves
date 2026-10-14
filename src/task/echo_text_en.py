"""Reads English echo substat rows from OCR boxes.

OCR on the upgrade panel splits or garbles names ("Energy" + "Regen", "XATK" from the icon,
"HeayvyAttackDMg" + "onus"), and long names wrap ("Resonance Skill DMG" / "Bonus"). Each value box
anchors a row; every name fragment joins the nearest row at or above it. The row's name is then
matched to the closest known substat among those whose legal rolls fit the value, so the value
settles flat vs % and Crit Rate vs Crit DMG, and the name only has to pick between stats that
share a roll table.
"""
import re
from difflib import SequenceMatcher

from src.task import echo_odds

NUMBER = re.compile(r"^[\d.%％ ]+$")
PLACEHOLDER = re.compile(r"upgrade|activate|substat", re.I)

EN_NAMES = {
    'crit_rate': 'crit rate',
    'crit_dmg': 'crit dmg',
    'atk_pct': 'atk', 'atk_flat': 'atk',
    'hp_pct': 'hp', 'hp_flat': 'hp',
    'def_pct': 'def', 'def_flat': 'def',
    'energy_regen': 'energy regen',
    'basic_dmg': 'basic attack dmg bonus',
    'heavy_dmg': 'heavy attack dmg bonus',
    'skill_dmg': 'resonance skill dmg bonus',
    'liberation_dmg': 'resonance liberation dmg bonus',
}
PCT_IDS = {'crit_rate', 'crit_dmg', 'atk_pct', 'hp_pct', 'def_pct', 'energy_regen',
           'basic_dmg', 'heavy_dmg', 'skill_dmg', 'liberation_dmg'}
ID_TO_ZH = {v: k for k, v in echo_odds.ZH_TO_ID.items()}
ID_TO_ZH.update({'atk_pct': '攻击', 'hp_pct': '生命', 'def_pct': '防御'})  # % on the value marks the percent form
MIN_SIMILARITY = 0.5


def _letters(text):
    return re.sub(r'[^a-z]', '', text.lower())


def _center_y(box):
    return box.y + box.height / 2


def _parse_number(text):
    try:
        return float(text.replace('％', '%').replace('%', '').strip())
    except ValueError:
        return None


def identify(name_text, value_text):
    """Return the substat id for a row's name and value text, or raise ValueError."""
    value = _parse_number(value_text)
    if value is None:
        raise ValueError(f'unreadable value {value_text!r}')
    pct = '%' in value_text or '％' in value_text
    letters = _letters(name_text)
    if not letters:
        raise ValueError(f'no name read for value {value_text!r}')
    candidates = []
    for stat_id, en in EN_NAMES.items():
        if (stat_id in PCT_IDS) != pct:
            continue
        legal = echo_odds.nearest_value(stat_id, value)
        if abs(legal - value) > echo_odds.roll_step(stat_id) / 2:
            continue
        similarity = SequenceMatcher(None, letters, _letters(en)).ratio()
        candidates.append((similarity, stat_id))
    if not candidates:
        raise ValueError(f'{name_text!r} {value_text!r} is not a possible roll')
    candidates.sort(reverse=True)
    best_score, best_id = candidates[0]
    if best_score < MIN_SIMILARITY:
        raise ValueError(f'cannot tell which substat {name_text!r} {value_text!r} is')
    return best_id


def parse_rows(boxes, row_tolerance):
    """Group OCR boxes into (name_box, value_box, stat_id) rows, top to bottom.

    row_tolerance: how far (in pixels) a name may sit above its value's centre and still belong to it.
    The returned name_box is the row's first fragment, renamed to EnhanceEchoTask's canonical
    Chinese name so the existing checks can use it.
    """
    values = sorted([b for b in boxes if NUMBER.match(b.name.strip())], key=_center_y)
    names = [b for b in boxes if not NUMBER.match(b.name.strip())
             and not PLACEHOLDER.search(b.name) and re.search('[A-Za-z]', b.name)]
    rows = {id(v): [] for v in values}
    for n in names:
        owners = [v for v in values if _center_y(v) - row_tolerance <= _center_y(n)]
        if not owners:
            continue  # a fragment above the first substat row (main stat spill-over)
        rows[id(owners[-1])].append(n)
    result = []
    for v in values:
        parts = sorted(rows[id(v)], key=lambda b: (round(_center_y(b) / max(row_tolerance, 1)), b.x))
        if not parts:
            raise ValueError(f'no name found for value {v.name!r}')
        text = ' '.join(p.name for p in parts)
        stat_id = identify(text, v.name)
        first = parts[0]
        first.name = ID_TO_ZH[stat_id]
        result.append((first, v, stat_id))
    return result
