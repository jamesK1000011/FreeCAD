# Slicing with another slicer

## The loop
1. The body needs a print setup. `slice_with { slicer: "prusaslicer" | "orcaslicer" | "bambustudio" }`.
2. Give the person's own profiles when they have them: `profiles: { machine, process, filament }` (JSON exported from OrcaSlicer or Bambu Studio), or `{ config }` (a PrusaSlicer .ini). Without them minimal profiles are written from the setup, and the slicer's defaults fill the rest.
3. The pauses for encased parts are written in. `hw_pauses` lists them.

## Traps
- A slicer not on this computer is refused, naming those present. AGENTLINK_ORCASLICER and AGENTLINK_BAMBUSTUDIO set their paths.
- Bambu printers pause on `M400 U1`; Klipper on `PAUSE` (give it as `pauseCommand`).
- OrcaSlicer and Bambu Studio are not run by this add-on's tests: check the first G-code they give.
