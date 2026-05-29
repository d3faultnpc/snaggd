Fill out the employer's application form on behalf of the candidate.

Form fields (JSON array):
{{FIELDS}}

Vacancy:
{{VACANCY}}

Return ONLY valid JSON mapping field index (string) to answer:
{"0": "answer", "1": "answer", ...}

Rules:
- Answer in the same language as the question (Russian → Russian, English → English)
- Use only facts from the candidate profile. Do not invent skills or experience
- text / textarea: 1–3 sentences, specific and concrete
- number fields (years of experience, etc.): return only the digit
- salary / compensation questions: use candidate's desired_salary from profile; match vacancy domain (fintech → 250k, telecom → 180k, default → 220k); for textarea: 1–2 sentences in Russian, factual and without filler; for short input/number field: number only (e.g. 220000)
- If a question is unclear or unanswerable from the profile: return empty string ""
