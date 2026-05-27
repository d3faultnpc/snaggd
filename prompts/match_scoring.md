Score how well the candidate matches the vacancy below.

The vacancy context may include employer metadata (company name, HH rating).
Use the HH Employer Rating as a signal in your assessment:
- Rating ≥ 4.5 → add signal "top_employer"
- Rating 3.5–4.4 → neutral, no extra signal
- Rating < 3.5 → add signal "low_rated_employer"
- "no reviews on HH" → add signal "no_hh_reviews"
Do NOT change the score based on rating alone — it is a signal, not a score modifier.

Also check if the vacancy belongs to any blocked category listed under
"stop_categories" in the candidate's JOB PREFERENCES (see system prompt).
If a match is found, set stop_match to the matched category name.
If the stop_categories list is absent or no match is found, set stop_match to null.

Return ONLY valid JSON, no markdown fences:
{
  "score": <integer 0–100>,
  "matched_skills": ["skill present in both profile and vacancy"],
  "gaps": ["requirement in vacancy missing from profile"],
  "signals": ["3–5 short tags characterizing this vacancy's domain, context, and product type"],
  "stop_match": null,
  "vacancy_role_type": "contribution style of this vacancy (use the same vocabulary as the candidate's role_type when possible)",
  "role_type_match": true
}

Scoring guide:
- 80–100: strong match — most key requirements met AND domain aligns with candidate's background
- 60–79: good match — solid skills overlap, minor gaps or adjacent domain
- 40–59: partial match — transferable skills but notable domain or experience gaps
- 0–39: poor fit — major mismatch in skills or domain

Domain alignment (apply BEFORE finalising the score):
Compare the vacancy's primary domain and product type against the candidate's domain and
background as described in CANDIDATE PROFILE.
- Same or closely related domain / product type → no modifier
- Adjacent: transferable skills, overlapping patterns → –5 to –10 points
- Clear mismatch: substantial domain gap, different industry patterns → –20 to –30 points
Do not apply penalties mechanically by industry label — consider product type overlap
and how transferable the candidate's actual experience is to this specific context.

Role type alignment (apply AFTER domain alignment):
Look for candidate's role_type in CANDIDATE PROFILE → Career Profile section.
Classify the vacancy's required contribution style and compare it to the candidate's role_type.
Use the same vocabulary as the candidate's role_type where possible.
- Same contribution style or adjacent → no modifier
- Clear mismatch in how value is created → –10 to –20 points
Set vacancy_role_type to the classified type.
Set role_type_match to true/false. Use null only if candidate's role_type is absent.

Baseline skills common to most vacancies in this field are NOT differentiators.
Do NOT use them as strong match signals — they are table stakes, not evidence of fit.
Focus on domain depth, specific product or context expertise, and the candidate's actual
track record that goes beyond the baseline for this type of role.

stop_match examples:
- Vacancy at "1x Bet" or "Pin-Up" with no explicit keyword → stop_match: "gambling"
- Vacancy mentioning "iGaming", "betting", "casino" → stop_match: "gambling"
- Vacancy clearly in MLM/network marketing → stop_match: "mlm"
- Normal vacancy → stop_match: null
