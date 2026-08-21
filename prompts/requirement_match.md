For each requirement below, say whether the candidate profile in your context
establishes it. You are not scoring anything — return no numbers of any kind.

For each requirement, by its index:
- `"met"` — the profile shows this, and you can quote where.
- `"partial"` — the profile shows something adjacent or smaller, and you can quote it.
- `"absent"` — the profile addresses this area and does not have it.
- `"silent"` — the profile says nothing either way.

The difference between `absent` and `silent` is the one that matters, and it is not a
matter of degree. `absent` means you looked at what the person does and this is not
among it. `silent` means the profile never speaks to it at all — a barista's profile
says nothing about punctuality, and reading that silence as a failure would invent a
shortcoming out of a document that simply does not discuss it. Most requirements a
short profile does not cover are `silent`, not `absent`.

`evidence` is a phrase copied from the profile, for `met` and `partial` only. If you
cannot copy one, the verdict is not `met`.

Blocked categories: the categories on the candidate's own `stop_categories:` line are
the entire vocabulary available to you, and a block has to rest on evidence — `text`
with a phrase quoted from the vacancy, or `company_knowledge` with the fact you rely
on. A neighbouring field is not the field. A B2B vendor selling into a category is in
its own business, not its customer's. If unsure, do not block.

Return ONLY valid JSON, no markdown fences:
{
  "verdicts": [{"i": 0, "verdict": "met", "evidence": "<PHRASE FROM THE PROFILE>"}],
  "matched_skills": ["<SKILL PRESENT IN BOTH>"],
  "role_type_match": true,
  "stop_match": null,
  "stop_basis": null,
  "stop_evidence": null
}

Return one verdict per requirement, using the index given. `role_type_match` is null
when the profile states no role_type at all.

REQUIREMENTS:
{{REQUIREMENTS}}
