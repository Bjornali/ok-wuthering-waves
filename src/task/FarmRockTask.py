import time

from ok import Logger
from src.task.FarmMapTask import FarmMapTask

logger = Logger.get_logger(__name__)


class FarmRockTask(FarmMapTask):
    """Walks a star path on the big map and breaks the rock marked by each star.

    Setup in game: place a diamond custom marker where you stand and a star custom marker
    on every rock, then open the big map and start the task.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "🪨 Farm Rocks with Star Path"
        self.description = ("Walks a star path on the big map and breaks the rock at every star. "
                            "Stand on a diamond marker, mark each rock with a star, then start on the map screen.")
        self.act_at_stars = True
        default_config = {
            'Arrival Distance': 3.0,
            'Max Distance Between Stars': 20.0,
            'Approach Time': 0.3,
            'Normal Attacks': 5,
            'Attack Interval': 0.35,
            'Use Resonance Skill': False,
            'Use Echo Skill': False,
            'Pick Up Time': 2.0,
        }
        default_config.update(self.default_config)
        self.default_config = default_config
        self.config_description = {
            'Arrival Distance': 'How close counts as reaching a star, in % of screen height on the big map. '
                                'Lower stops closer to the rock but can overshoot.',
            'Max Distance Between Stars': 'Stars further than this from the previous star, in % of screen height '
                                          'on the big map, are dropped from the path.',
            'Approach Time': 'Seconds to keep walking forward after reaching a star, before attacking.',
            'Normal Attacks': 'Normal attacks on each rock.',
            'Attack Interval': 'Seconds between normal attacks.',
            'Use Resonance Skill': 'Also use the Resonance Skill on each rock.',
            'Use Echo Skill': 'Also use the Echo Skill on each rock.',
            'Pick Up Time': 'Seconds to watch for an F pick-up prompt after breaking a rock.',
        }

    @property
    def star_move_distance_threshold(self):
        return self.height_of_screen(float(self.config.get('Arrival Distance', 3.0)) / 100)

    @property
    def star_max_hop_distance(self):
        return self.height_of_screen(float(self.config.get('Max Distance Between Stars', 20.0)) / 100)

    def run(self):
        self.info_set('Rocks Hit', 0)
        self.info_set('Picked Up', 0)
        try:
            super().run()
        finally:
            self.log_info(f"Rock run finished: {self.info_get('Rocks Hit', 0)} rocks hit, "
                          f"{self.info_get('Picked Up', 0)} pick-ups", notify=True)

    def on_star_reached(self, star):
        self.break_rock()

    def break_rock(self):
        approach_time = float(self.config.get('Approach Time', 0.3))
        if approach_time > 0:
            self.send_key('w', down_time=approach_time, after_sleep=0.1)

        attack_interval = float(self.config.get('Attack Interval', 0.35))
        for _ in range(int(self.config.get('Normal Attacks', 5))):
            self.click(after_sleep=attack_interval)

        if self.config.get('Use Resonance Skill'):
            self.send_key(self.get_resonance_key(), after_sleep=0.8)
        if self.config.get('Use Echo Skill'):
            self.send_key(self.get_echo_key(), after_sleep=0.8)

        self.info_incr('Rocks Hit')
        self.pick_up_drops()

    def pick_up_drops(self):
        end = time.time() + float(self.config.get('Pick Up Time', 2.0))
        while time.time() < end:
            if self.pick_f(handle_claim=False):
                self.info_incr('Picked Up')
            self.sleep(0.2)
