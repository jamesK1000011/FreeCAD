# SPDX-License-Identifier: LGPL-2.1-or-later
# The information rules of a pack written as buildingSMART IDS 1.0, for
# ifctester and other BIM checkers: each info.required rule a specification,
# its subject kind the applicability (an IFC entity), each "Pset.Property"
# fact a property requirement and "material" a material requirement. Facts
# with no IFC home (a species asked of the person) stay out, and are listed.

from xml.sax.saxutils import escape, quoteattr

ENTITY = {"member": ["IFCBEAM", "IFCMEMBER", "IFCCOLUMN"], "space": ["IFCSPACE"], "opening": ["IFCWINDOW", "IFCDOOR"],
          "wall": ["IFCWALL"], "slab": ["IFCSLAB"], "roof": ["IFCROOF"], "covering": ["IFCCOVERING"]}
NS = 'xmlns="http://standards.buildingsmart.org/IDS" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://standards.buildingsmart.org/IDS http://standards.buildingsmart.org/IDS/1.0/ids.xsd"'


def _sv(v):
    return f"<simpleValue>{escape(str(v))}</simpleValue>"


def write(rules, title, version="1.0", author="agentlink@example.invalid"):
    """(xml text, skipped [(rule, fact, why)])"""
    specs, skipped = [], []
    for rid, r in sorted(rules.items()):
        if r.get("check") != "info.required":
            continue
        kind = (r.get("applies") or {}).get("kind")
        ents = ENTITY.get(kind)
        if not ents:
            skipped.append((rid, None, f"no IFC entity for kind {kind!r}"))
            continue
        reqs = []
        for f in (r.get("params") or {}).get("facts") or r.get("needs") or []:
            if f == "material":
                reqs.append('<material cardinality="required"/>')
            elif "." in f and f.split(".", 1)[0].lower().startswith(("pset", "qto", "agentlink")):
                pset, name = f.split(".", 1)
                reqs.append(f'<property cardinality="required"><propertySet>{_sv(pset)}</propertySet><baseName>{_sv(name)}</baseName></property>')
            else:
                skipped.append((rid, f, "not an IFC property: the person is asked for it instead"))
        if not reqs:
            continue
        for e in ents:
            name = quoteattr(str(r.get("title") or rid))
            ident = quoteattr(str(rid))
            desc = quoteattr(str(r.get("cite") or ""))
            specs.append(f'<specification name={name} ifcVersion="IFC4 IFC2X3" identifier={ident}'
                         + (f" description={desc}" if r.get("cite") else "") + ">"
                         + f"<applicability minOccurs=\"0\" maxOccurs=\"unbounded\"><entity><name>{_sv(e)}</name></entity></applicability>"
                         + "<requirements>" + "".join(reqs) + "</requirements></specification>")
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<ids {NS}><info><title>{escape(title)}</title><version>{escape(version)}</version>'
           f"<description>Information rules from Agent Link rule packs. Planning use; not an approval.</description></info>"
           f"<specifications>{''.join(specs)}</specifications></ids>\n")
    return xml, skipped
