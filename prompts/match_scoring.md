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
  "signals": ["domain tags: platform, b2b, b2c, fintech, admin_systems, growth, etc."],
  "stop_match": null
}

Scoring guide:
- 80–100: strong match — most key requirements met AND domain aligns with candidate's background
- 60–79: good match — solid skills overlap, minor gaps or adjacent domain
- 40–59: partial match — transferable skills but notable domain or experience gaps
- 0–39: poor fit — major mismatch in skills or domain

Domain alignment (apply BEFORE finalising the score):
Check the vacancy's primary domain against the candidate's domain (from CANDIDATE PROFILE) and preferred domains (from JOB PREFERENCES).
- Domain matches candidate profile (fintech, marketplace, B2B2C platform, payment systems, admin/back-office tools) → no modifier
- Adjacent domain (B2B SaaS, e-commerce, growth product, internal tools) → –5 to –10 points
- Clearly different domain (media, music/streaming, gaming, content, healthcare, logistics, real estate, travel) → –20 to –30 points

Generic PM skills (A/B testing, roadmap, cross-functional coordination) are present in almost every PM role.
Do NOT use them as strong match signals — they are baseline, not differentiators.
Focus on domain expertise, specific product types, and the candidate's actual track record.

stop_match examples:
- Vacancy at "1x Bet" or "Pin-Up" with no explicit keyword → stop_match: "gambling"
- Vacancy mentioning "iGaming", "betting", "casino" → stop_match: "gambling"
- Vacancy clearly in MLM/network marketing → stop_match: "mlm"
- Normal vacancy → stop_match: null
