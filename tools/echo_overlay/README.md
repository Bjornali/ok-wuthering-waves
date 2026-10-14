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

Edit `roster.json` to change a character's weights or add a character (same fields as the others).
Set `"scoring": "range"` in `overlay_settings.json` to use the Echo Diagnostics page's scoring,
where a minimum roll counts as zero.
