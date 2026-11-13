# Building-code checks (planning, never approval)

These tools check a BIM model against rule packs and say what an engineer will want to look at. They never approve anything: every answer carries the planning-estimate notice, and the person's architect or engineer reviews, approves and stamps. Say that plainly whenever you report results. A finding in the state `compliant` means only "within the checked limits" of the rules in force for the facts given: read its `scope` and `not_checked` aloud with it, and never call it a pass or a sign-off.

## The loop
1. Model the building with the `bim` tools: walls, slabs, rooms (`bim_space`), openings, and framing members as `bim_structure` beams (width = thickness, depth = depth) along their run.
2. `code_project { jurisdiction, date }`: the packs in force there and then (the jurisdiction matches in any case). `code_packs` lists packs; `code_packs import` adds one. The shipped pack is an example with made-up numbers (jurisdiction XX): say so if you use it. With no rules in force `code_check` refuses rather than answer an empty, clean-looking result; packs refused as malformed are listed.
3. `code_facts`: what the checks will read, and where each value came from.
4. `code_check`: findings, worst first, in five states.
5. `code_missing`: the questions that would decide what is open. Ask the person a few at a time, in plain words. Never guess a value they should measure. Store their answers with `code_answer` (`subjects` for one answer to several), then check again.
6. `code_packet` when the open items are engineer's calls: the folder for the professional. It always runs every check fresh and unfiltered. Run `bim_export` and `td_export` first and pass their paths so the model and drawings go in.

## Units and plausible values
- `code_answer` converts on the way in and holds lengths in mm, areas in mm², loads in kPa and design values in MPa. Give a unit whenever the person did (`{ value: 20, unit: "ft" }`, `{ value: 875, unit: "psi" }`); a unit the engine does not know is refused. With no unit, the held unit is meant.
- A value outside its plausible band (a span of 3.6, an Fb of 875 with no unit) is not judged: it comes back as a question ("is this in mm?"). Ask the person; give it with its unit, or `confirmed: true` if it really is that number.
- `pm` (plus-minus) is 0 or more. `factor.*` answers must sit inside their NDS ranges (CD 0.9–2.0, Cr 1.0–1.15 …).
- Project-wide values (a ground snow load, a local live load) go to subject `project`: they join the packs' values, and one looser than a pack's is refused. Answers for a label the model no longer has are warned about, not checked; give `kind` to check something not modelled.

## Questions about scope
- An opening the model cannot say is an escape opening (`escape` unknown) and a room of undefined use get a question from each rule that might apply, never silence. Answer `escape: true/false` or `use: "habitable"` (or another use) with `code_answer`. A known value that does not match makes the rule `not_applicable`.

## Packs and layers
- The base value says which way is stricter (`stricter: higher | lower`, required on a model pack's numbers). A higher layer may tighten; a looser value is refused as a conflict, unless a state or local amendment loosens it with `op: "loosen"` and a citation — then every finding that uses it shows `loosened`. A contractor never loosens.
- Two packs of the same `code` and layer (two editions) both in force are refused: set `superseded` (after `effective`) on the older. `jurisdiction: "*"` is only for model or example packs.

## Stairs and garages
- A `bim_stairs` flight gives its riser height, tread depth (the going) and width from its own sizes; headroom and whether it has a handrail are the person's to say (`code_answer`).
- Which walls and doors separate the garage from the home is never guessed: ask, then answer `separates_garage: true` on each. Their checks (the lining's thickness, a solid or fire-rated self-closing door) apply only after that.

## Information and IDS
- Rules of the information kind ask that the model carries data: a wall's material, its fire rating (`Pset_WallCommon.FireRating`, set with `bim_ifc properties`). `code_packs { action: "ids" }` writes those rules as a buildingSMART IDS 1.0 file, which any IDS checker (ifctester, Bonsai) runs against the exported IFC.

## Structural
- `code_span` checks one wood member by hand on a simple span (bending, shear, deflection, bearing), in two load cases: dead plus live (or snow) at CD, and dead alone at CD 0.9; the worse governs. Use it for what-ifs ("would a 2x8 do?"). Design values (Fb, Fv, E, Fc_perp) come from the person: the species and grade on the stamp and its table or supplier sheet. None are assumed. Cr 1.15 applies only to dimension lumber (2x to 4x) at 610 mm or closer, never to timbers. Every value the engine fills in itself (bearing length, Cr, CD, deflection limits, the roof snow factor) is listed under `defaults`; tell the person. Each member finding lists what it did not check (connections, ledgers, notches, lateral stability, uplift …).
- `code_fem` (or `fem: true`) builds a CalculiX beam model beside the hand check. It is a rough look: if the two differ by more than 15 %, the inputs disagree somewhere.
- Engineered trusses are never judged: any member answered `truss: true` (or found as one) is manual review, needing the truss maker's repair detail or an engineer.

## States
compliant (within the checked limits only — never a sign-off) · non_compliant · insufficient_information (a value is missing, implausible, or whether the rule applies is unknown: ask) · manual_review_required (an engineer's call, a truss, a measurement within tolerance of the limit, or an assumed value) · not_applicable · error (the check itself failed, or a value was refused — never read it as a pass). A result resting on an assumed value of any fact the check reads is never compliant.
