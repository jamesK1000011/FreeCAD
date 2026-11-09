# Milling parts

## The loop
1. Ask how it is milled, then `setup add { type: "mill", bodies, settings: { axes: "3" | "3+2", minTool, grade } }`.
2. `mill_check` after every change. Give `material`, `stock: [x, y, z]` and `reach` when you know them; what is not given comes back under `assumed`.
3. `mill_export` for STEP and a setup sheet in Markdown.

## Traps
- Directions are the sides the tool comes from. axes 3 means +z only: anything facing down or hidden under an overhang is an undercut. Turn the part, or add directions (`directions: ["+z", "-z"]` for a flip).
- Faces on the stock's own outside are left by the stock and not judged.
- Inside vertical corners come out as round as the tool: give them a radius a little over the least tool's.
- A hole under the tool's diameter is drilled, not milled.
