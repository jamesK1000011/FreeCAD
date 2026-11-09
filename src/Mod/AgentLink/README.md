<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->
# Agent Link for FreeCAD (spike)

An agent drives FreeCAD over the Model Context Protocol, in ScanRuler's
manner: few tools, one call per job, small answers in millimetres, and
refusals that say what to do instead.

| Tool | What it does |
|---|---|
| `session_state` | What the document holds: each body's volume, area, faces, solids and size |
| `gen_make` | A Gridfinity bin made to size (`measure: true` sizes it without making it). Magnets come back as encased, with the height to pause at |
| `body_check` | Valid, closed, one solid, no sliver faces or very short edges |
| `setup` | How each part is made (print, cut, mill) and that process's settings, kept with the document |
| `part_spec` | The limits parts must meet (least wall, mass) and what each is made of |
| `dfm_check` | Printability with the part's print setup: thin walls, overhangs, bridges, small holes, bed contact, tall and thin, build volume. `waive` keeps a finding accepted on purpose |
| `part_review` | One verdict: pass, fix or unknown. How the part is made decides which checks judge it |
| `fit` | Hole and peg sizes for press, snug, slide and free fits; the printer's calibration; tolerance stacks |
| `hw_hole` | Clearance, counterbore, countersink, nut-trap, heat-set insert and magnet holes at standard sizes |
| `slice` | PrusaSlicer's command line with the setup's settings: time, filament and layers, with the magnet pause written into the G-code |
| `export_file` | STEP or STL, read back to verify |
| `history_diff` | What changed since a step, per body |

## Two ways to run it

**Headless.** Use a Python that can import FreeCAD (conda-forge's `freecad`,
or `FREECAD_LIB` pointing at FreeCAD's `lib`), or FreeCAD's own command line:

```
python src/Mod/AgentLink/agentlink_headless.py --out ~/Documents/AgentLink
AGENTLINK_OUT=~/Documents/AgentLink FreeCADCmd src/Mod/AgentLink/agentlink_headless.py
```

**In the FreeCAD window, so you can watch.** Copy or link this folder into
FreeCAD's `Mod` folder. Pick the Agent Link workbench and press **Start
link**; it listens on 127.0.0.1:7318 only. Then the MCP client starts the
relay:

```
python src/Mod/AgentLink/agentlink_headless.py --connect 7318
```

Each call runs on FreeCAD's own thread, in the document you have open.

## Tests and the bake-off

`python -m unittest discover -s src/Mod/AgentLink/tests` runs the protocol tests
anywhere. The tool tests need FreeCAD and are skipped without it.

The workflow `.github/workflows/freecad-bakeoff.yml` runs the tool tests
and the bake-off on GitHub's machines on every push to `freecad-spike`.
The bake-off is one Gridfinity brief (`scripts/bakeoff/brief.mjs`) given to
this add-on headless and to ScanRuler in headless Chrome. ScanRuler's
Deviation workspace then compares the two bins both ways, and the
scorecard goes to the run's summary page.

## Licence

LGPL-2.1-or-later (see `LICENSE`), so it can sit beside FreeCAD's
own code. It uses only FreeCAD's public Python API and copies none of
ScanRuler's code. The Gridfinity dimensions come from the open
specification.

FreeCAD's `AI_POLICY.md` does not accept AI-generated pull requests. This
add-on is not for upstreaming as it is.
