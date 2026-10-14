# Echo odds overlay

A small window that sits on top of Wuthering Waves while you tune echoes yourself. It reads the
echo from the screen and says **KEEP GOING**, **STOP** or **TOSS-UP**, with the odds behind it.
It only reads the screen; it never clicks or touches the game.

## Run

Double-click `run_overlay.bat`. The first run installs its packages into a `.venv` folder.
`run_overlay.bat debug` runs it with a console so errors are visible.

Play in windowed or borderless mode at 16:9. Exclusive fullscreen hides overlays.

## Use

- **Echo Upgrade screen**: tune as usual. After each +5 the call updates.
- **Bag > Echoes**: select an echo to judge it. If it says "Equipped by <name>", the overlay
  switches to that character when *Follow equipped* is ticked.
- Pick the character in the drop-down for new, unequipped echoes.
- Drag the window by its title bar. **snapshot** saves the current screen and reading to
  `snapshots/`; send those if a reading is wrong.

## How the call is made

Each substat is credited by how much of a max roll it is (6.3% Crit Rate counts 60% of 10.5%),
times the character's weight in `roster.json`. The echo's score is out of 100. The bar is the score
only 5% of fresh echoes reach for that character. At each step it simulates 20,000 ways the echo
could finish and compares its odds of clearing the bar with a fresh echo's 5%:
1.5x or better is KEEP GOING, 0.67x or worse is STOP, in between is TOSS-UP.
Roll values and their odds come from Kuro Games' published probability disclosure.

## Characters

`roster.json` covers all 60 playable Resonators as of 3.7, including the four Rover forms and Suoming
(3.7 phase 2, pre-release guide). Six have hand-reviewed weights (Calcharo, Hiyuki, Mortefi,
Shorekeeper, Verina, Yinlin). The rest come from each character's substat priority in the
wuwabuild.com / prydwen.gg build guides (`builds_source.json`), turned into weights by
`make_roster.py`; the rules are at the top of that file. The overlay shows each character's
priority line under the drop-down.

To change a character, edit their line in `builds_source.json` and run `python make_roster.py`,
or edit their weights in `roster.json` directly. Rover shows up in the bag under your own name,
so pick the Rover form by hand.
Set `"scoring": "range"` in `overlay_settings.json` to use the Echo Diagnostics page's scoring,
where a minimum roll counts as zero.
