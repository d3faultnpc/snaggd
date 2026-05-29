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
- salary / compensation questions: output the salary expectation directly — do NOT describe the profile or explain your reasoning. Match vacancy domain: fintech → "рассматриваю от 250 тысяч на руки", telecom → "рассматриваю от 180 тысяч на руки", default/unknown → "рассматриваю от 220 тысяч на руки". For textarea add: ", зависит от состава задач и компенсационного пакета". For short input: number only (e.g. 220000)
- If a question is unclear or unanswerable from the profile: return empty string ""
