from ok import Logger
from src.task import echo_odds
from src.task.EnhanceEchoTask import EnhanceEchoTask, parse_number

logger = Logger.get_logger(__name__)

RULE_KEYS = ['必须有双爆', '双爆出现之前必须全有效词条', '双爆总计>=', '首条双爆>=', '有效词条>=',
             '第一条必须为有效词条', '有效词条']
SCORING_OPTIONS = ['Value / max roll', 'Same as Echo Diagnostics page']
TOSS_UP_OPTIONS = ['Keep tuning', 'Discard']


def normalize_property(name, value_text):
    """Map an OCR'd substat name to EnhanceEchoTask's canonical Chinese name."""
    pct = '%' in value_text or '％' in value_text
    if '暴击伤害' in name:
        return '暴击伤害'
    if '暴击' in name:
        return '暴击'
    if '攻击' in name:
        return '攻击百分比' if pct else '攻击'
    if '生命' in name:
        return '生命百分比' if pct else '生命'
    if '防御' in name:
        return '防御百分比' if pct else '防御'
    if '效率' in name:
        return '共鸣效率'
    if '普攻' in name:
        return '普攻伤害加成'
    if '重击' in name:
        return '重击伤害加成'
    if '解放' in name:
        return '共鸣解放伤害加成'
    if '技能' in name:
        return '共鸣技能伤害加成'
    return name


class EnhanceEchoOddsTask(EnhanceEchoTask):
    """Batch enhance, deciding keep/discard at every tuning step with the Echo Diagnostics odds."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "⬆️ Enhance Echoes by Odds (game language: Chinese)"
        self.description = ("Batch enhance that keeps or discards each echo by its odds of reaching the top-5% bar "
                            "for the chosen character, versus a fresh echo. Open the bag (B), filter the echoes for "
                            "that character, sort by level from +0, then start. Game and ok-ww must be in Chinese.")
        for key in RULE_KEYS:
            self.default_config.pop(key, None)
            self.config_description.pop(key, None)
        self.config_type.pop('有效词条', None)
        names = list(echo_odds.load_roster())
        self.default_config.update({
            'Character': 'Hiyuki' if 'Hiyuki' in names else names[0],
            'Scoring': SCORING_OPTIONS[0],
            'On Toss-up': TOSS_UP_OPTIONS[0],
            'Samples': 20000,
        })
        self.config_type['Character'] = {'type': 'drop_down', 'options': names}
        self.config_type['Scoring'] = {'type': 'drop_down', 'options': SCORING_OPTIONS}
        self.config_type['On Toss-up'] = {'type': 'drop_down', 'options': TOSS_UP_OPTIONS}
        self.config_description.update({
            'Character': 'Whose substat weights to judge the filtered echoes by (from the Echo Diagnostics roster).',
            'Scoring': 'Value / max roll credits a minimum Crit roll as 60% of a max roll. '
                       'The page credits minimum rolls as zero.',
            'On Toss-up': 'What to do when this echo and a fresh one have similar odds.',
            'Samples': 'Simulated echoes per decision. More is steadier but slower.',
        })
        self._odds = None
        self._odds_key = None

    def odds(self):
        key = (self.config.get('Character'), self.config.get('Scoring'), int(self.config.get('Samples', 20000)))
        if key != self._odds_key:
            roster = echo_odds.load_roster()
            if key[0] not in roster:
                raise Exception(f'Character {key[0]} is not in the roster')
            norm = 'range' if key[1] == SCORING_OPTIONS[1] else 'max'
            self._odds = echo_odds.EchoOdds(roster[key[0]]['weights'], samples=max(2000, key[2]), norm=norm)
            self._odds_key = key
            self.log_info(f'{key[0]}: top-5% bar {self._odds.target:.1f} ({norm} scoring)')
        return self._odds

    def read_substats(self, properties, values):
        """Pair each OCR'd property with the value on its row and snap it to a legal roll."""
        unmatched = list(values)
        substats = []
        for prop in properties:
            if not unmatched:
                raise Exception(f'No value found for substat {prop.name}')
            closest = min(unmatched, key=lambda v: abs(prop.y - v.y))
            unmatched.remove(closest)
            canonical = normalize_property(prop.name, closest.name)
            stat_id = echo_odds.ZH_TO_ID.get(canonical)
            if stat_id is None:
                raise Exception(f'Unknown substat {prop.name} ({closest.name})')
            raw = parse_number(closest.name)
            value = echo_odds.nearest_value(stat_id, raw)
            if abs(value - raw) > echo_odds.roll_step(stat_id) / 2:
                raise Exception(f'Substat {prop.name} read as {closest.name}, not a possible roll')
            substats.append((stat_id, value))
        return substats

    def check_echo_stats(self, properties, values):
        self.fail_reason = ""
        substats = self.read_substats(properties, values)
        result = self.odds().judge(substats)
        verdict = result['verdict']
        keep = verdict == 'keep' or (verdict == 'mixed' and self.config.get('On Toss-up') == TOSS_UP_OPTIONS[0])
        summary = (f"score {result['progress']:.1f} / bar {result['target']:.1f}, "
                   f"{result['p_continue']:.1f}% vs fresh {result['p_fresh']:.1f}%")
        self.info_set('Substats', ', '.join(f'{t} {v}' for t, v in substats))
        self.info_set('Odds', f'{verdict}: {summary}')
        if keep:
            self.log_info(f'{verdict}, continue: {summary}')
        else:
            self.fail_reason = f"{verdict}_{result['p_continue']:.1f}vs{result['p_fresh']:.1f}"
            self.log_info(f'{verdict}, discard: {summary}')
        return keep
