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
- 80–100: strong match, most key requirements met
- 60–79: good match, minor gaps
- 40–59: partial match, notable gaps
- 0–39: poor fit, major mismatch

stop_match examples:
- Vacancy at "1x Bet" or "Pin-Up" with no explicit keyword → stop_match: "gambling"
- Vacancy mentioning "iGaming", "betting", "casino" → stop_match: "gambling"
- Vacancy clearly in MLM/network marketing → stop_match: "mlm"
- Normal vacancy → stop_match: null
