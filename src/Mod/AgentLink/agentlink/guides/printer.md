# Printers on the network

## The loop
1. `printer_add { name, kind, url, key }`. kind: octoprint, moonraker (klipper), prusalink. The key is kept in the session's folder and never shown again.
2. `printer_status` before sending: is it idle, is the bed clear (ask the person).
3. `printer_send { file }` uploads. It starts only with `start: true` and `confirm: "start <name>"`.

## Traps
- Starting, pausing and stopping a print are the person's calls. Ask, and use their words in `confirm`. Never start a print on your own.
- Bambu Lab printers are refused: they take jobs over LAN mode or from Bambu Studio. Slice with `slice_with { slicer: "bambustudio" }` and send from there.
- `printer_stop` cancels: the job cannot be resumed.
