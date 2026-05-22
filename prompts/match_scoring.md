Score how well the candidate matches the vacancy below.

Return ONLY valid JSON, no markdown fences:
{
  "score": <integer 0–100>,
  "matched_skills": ["skill present in both profile and vacancy"],
  "gaps": ["requirement in vacancy missing from profile"],
  "signals": ["domain tags: platform, b2b, b2c, fintech, admin_systems, growth, etc."]
}

Scoring guide:
- 80–100: strong match, most key requirements met
- 60–79: good match, minor gaps
- 40–59: partial match, notable gaps
- 0–39: poor fit, major mismatch
