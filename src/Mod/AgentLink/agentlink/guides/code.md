# Building-code checks (planning, never approval)

These tools check a BIM model against rule packs and say what an engineer will want to look at. They never approve anything: every answer carries the planning-estimate notice, and the person's architect or engineer reviews, approves and stamps. Say that plainly whenever you report results.

## The loop
1. Model the building with the `bim` tools: walls, slabs, rooms (`bim_space`), openings, and framing members as `bim_structure` beams (width = thickness, depth = depth) along their run.
2. `code_project { jurisdiction, date }`: the packs in force there and then. `code_packs` lists packs; `code_packs import` adds one. The shipped pack is an example with made-up numbers (jurisdiction XX): say so if you use it.
3. `code_facts`: what the checks will read, and where each value came from.
4. `code_check`: findings, worst first, in five states.
5. `code_missing`: the questions that would decide what is open. Ask the person a few at a time, in plain words. Never guess a value they should measure. Store their answers with `code_answer` (`subjects` for one answer to several), then check again.
6. `code_packet` when the open items are engineer's calls: the folder for the professional. Run `bim_export` and `td_export` first and pass their paths so the model and drawings go in.

## Stairs and garages
- A `bim_stairs` flight gives its riser height, tread depth (the going) and width from its own sizes; headroom and whether it has a handrail are the person's to say (`code_answer`).
- Which walls and doors separate the garage from the home is never guessed: ask, then answer `separates_garage: true` on each. Their checks (the lining's thickness, a solid or fire-rated self-closing door) apply only after that.

## Information and IDS
- Rules of the information kind ask that the model carries data: a wall's material, its fire rating (`Pset_WallCommon.FireRating`, set with `bim_ifc properties`). `code_packs { action: "ids" }` writes those rules as a buildingSMART IDS 1.0 file, which any IDS checker (ifctester, Bonsai) runs against the exported IFC.

## Structural
- `code_span` checks one wood member by hand (bending, shear, deflection, bearing). Use it for what-ifs ("would a 2x8 do?"). Design values (Fb, Fv, E, Fc_perp) come from the person: the species and grade on the stamp and its table or supplier sheet. None are assumed.
- `code_fem` (or `fem: true`) builds a CalculiX beam model beside the hand check. It is a rough look: if the two differ by more than 15 %, the inputs disagree somewhere.
- Engineered trusses are never judged: any member answered `truss: true` (or found as one) is manual review, needing the truss maker's repair detail or an engineer.

## States
compliant · non_compliant · insufficient_information (a value is missing: ask) · manual_review_required (an engineer's call, a truss, or a measurement within tolerance of the limit) · not_applicable · error (the check itself failed — never read it as a pass). A result resting on an assumed value is never compliant.
