Score how well the candidate matches the vacancy below.

The vacancy context may include employer metadata (company name, HH rating).
Use the HH Employer Rating as a signal in your assessment:
- Rating ≥ 4.5 → add signal "top_employer"
- Rating 3.5–4.4 → neutral, no extra signal
- Rating < 3.5 → add signal "low_rated_employer"
- "no reviews on HH" → add signal "no_hh_reviews"
Do NOT change the score based on rating alone — it is a signal, not a score modifier.

Blocked categories (stop_match)

The categories listed under "stop_categories" in the candidate's JOB PREFERENCES
(see system prompt) are the entire vocabulary available to you here. If that list
is absent, or the vacancy matches nothing on it, stop_match is null. Never invent
a category and never return one that is not on that list — a value outside it is
discarded and blocks nothing.

A block means the application is never sent, so it has to rest on evidence, and
your answer must say which kind:
- "text" — the vacancy's own wording establishes it. Quote the phrase in
  stop_evidence.
- "company_knowledge" — the text does not say it, but you know this employer
  operates in that category. State the fact in stop_evidence, e.g. "builds
  software for online casinos".

What is not evidence:
- An adjacent domain. Video games, entertainment, loyalty and VIP programmes,
  engagement mechanics, payments and high-risk fintech are not gambling.
  Adjacency belongs in signals, never in a block.
- A company whose CLIENT is in the blocked category. A B2B vendor is in its own
  business, not its customer's.
- A company name that resembles a known brand in the category. Reason from what
  a company does, never from what its name sounds like.
- Your own uncertainty. If unsure, do not block: return null and say why in
  signals.

If the signals you are producing for this vacancy contradict the category you
are about to block on, do not block.

Return ONLY valid JSON, no markdown fences. The block below is the SHAPE of your answer only —
every `<PLACEHOLDER>` must be replaced with your real analysis; never copy a placeholder verbatim:
{
  "score": <INTEGER_0_TO_100>,
  "matched_skills": ["<REAL_SKILL_1>", "<REAL_SKILL_2>"],
  "gaps": ["<REAL_GAP_1>", "<REAL_GAP_2>"],
  "signals": ["<REAL_TAG_1>", "<REAL_TAG_2>", "<REAL_TAG_3>"],
  "stop_match": null,
  "stop_basis": null,
  "stop_evidence": null,
  "vacancy_role_type": "<REAL_CONTRIBUTION_STYLE>",
  "role_type_match": true
}
Field meanings: matched_skills = skills present in both profile and vacancy. gaps = requirements
in the vacancy missing from the profile. signals = 3–5 short tags characterizing this vacancy's
domain, context, and product type. vacancy_role_type = this vacancy's contribution style, using
the candidate's role_type vocabulary where possible.

Scoring guide:
- 80–100: strong match — most key requirements met, and the domain aligns with the candidate's
  background where the profile states one (a profile that states no domain can still score here)
- 60–79: good match — solid skills overlap, minor gaps or adjacent domain
- 40–59: partial match — transferable skills but notable domain or experience gaps
- 0–39: poor fit — major mismatch in skills or domain

Domain alignment (apply BEFORE finalising the score):
If CANDIDATE PROFILE states no domain or industry background at all — apply NO modifier, and do
not treat the silence as a mismatch. Plenty of real professions are described without an industry
(a barista, a warehouse worker, a nurse), and a profile that names none is not evidence of
distance from this vacancy; it is evidence of nothing. Score on the skills and experience that
are actually stated.
Otherwise, compare the vacancy's primary domain and product type against the candidate's domain
and background as described in CANDIDATE PROFILE.
- Same or closely related domain / product type → no modifier
- Adjacent: transferable skills, overlapping patterns → –5 to –10 points
- Clear mismatch: substantial domain gap, different industry patterns → –20 to –30 points
Do not apply penalties mechanically by industry label — consider product type overlap
and how transferable the candidate's actual experience is to this specific context.
A low-confidence note under "Additional" (hints) does not by itself establish domain
alignment — treat it as real domain evidence only if a case or highlight in Work
Experience/Projects independently supports it. Same principle as aspiration alignment
below: an unconfirmed one-liner should not move the score on its own.

Aspiration alignment (apply after domain alignment, before role type):
If the candidate profile states a career aspiration (Career Profile → aspiration):
- If the vacancy's primary domain matches that aspiration AND the candidate profile demonstrates
  hands-on delivery evidence there (shipped work, concrete cases — not just the stated aspiration
  itself): → +5 to +10 points. Symmetric counterweight to the domain mismatch penalty above.
- If the vacancy has that aspiration's domain as a secondary signal (present in signals but not
  the primary domain) AND the candidate demonstrates hands-on delivery there: → reduce the clear
  domain mismatch penalty by 5–10 points (apply –10..–20 instead of –20..–30) instead of adding
  points — this reduces the penalty only, it does not add points when there is no mismatch.
- A vacancy's domain counts as "primary" when the product itself IS that domain (e.g. the product
  is an AI system, a medical research tool); "secondary" when it's a feature or tooling choice
  within a product whose primary domain is something else.
- Apply only when the candidate profile has real delivery evidence for the aspiration domain — an
  aspiration statement alone, with no supporting cases, should not move the score.
- Do NOT apply to a vacancy you are blocking (any non-null stop_match)

Role type alignment (apply AFTER domain alignment):
If candidate's role_type is absent or empty — apply NO modifier. Many real professions (e.g. a
barista, a dentist) don't map onto this vocabulary at all, and an absent value is not evidence of
a mismatch. Still classify vacancy_role_type below (used elsewhere, e.g. cover letter context)
and set role_type_match to null.
Otherwise: look for candidate's role_type in CANDIDATE PROFILE → Career Profile section.
Classify the vacancy's required contribution style and compare it to the candidate's role_type.
Use the same vocabulary as the candidate's role_type where possible.
- Same contribution style or adjacent → no modifier
- Clear mismatch in how value is created → –10 to –20 points
Set vacancy_role_type to the classified type.
Set role_type_match to true/false. Use null only if candidate's role_type is absent.

Final score after all modifiers: floor at 0, cap at 100.

Baseline skills common to most vacancies in this field are NOT differentiators.
Do NOT use them as strong match signals — they are table stakes, not evidence of fit.
Focus on domain depth, specific product or context expertise, and the candidate's actual
track record that goes beyond the baseline for this type of role.

stop_match examples, assuming "gambling" is on this candidate's own list:
- Text says "iGaming platform" / "betting" / "casino" → stop_match "gambling",
  stop_basis "text", stop_evidence the quoted phrase.
- A bookmaker whose posting never says so → stop_match "gambling", stop_basis
  "company_knowledge", stop_evidence what the company is known to operate.
- A B2B analytics vendor whose clients include casinos → stop_match null,
  signal "gambling_adjacent".
- A mobile app for a faith community, a payroll project at a bank, a video game
  with loyalty mechanics → stop_match null.
- "gambling" not on this candidate's list → stop_match null, whatever the text says.
