# SPDX-License-Identifier: LGPL-2.1-or-later
# Building-code checks for planning, not approval: rule packs that stack in
# layers (model code, state, local, contractor), facts read from the model
# with where each came from, check functions that judge them, and findings in
# five states plus error. Pure Python here; only families/code.py reads
# FreeCAD. Nothing in this package certifies anything: a licensed
# professional reviews, approves and stamps.

DISCLAIMER = "Planning estimate prepared with FreeCAD Agent Link. Not reviewed, approved or stamped by a licensed professional."
