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
- salary / compensation questions: read the candidate's salary expectation from the candidate profile (Desired Salary section) — do NOT describe the profile or explain your reasoning, output the expectation directly. Express it in the currency and period the specific question asks for (monthly/annual, gross/net, RUB/USD/etc. — infer from the question's own wording). If the profile gives domain-conditional ranges, pick the range matching this vacancy's domain; otherwise use the profile's default range. For textarea add: ", зависит от состава задач и компенсационного пакета". For short input: number only (e.g. 220000). If the profile has no salary information at all: do not invent a number — return an empty string
- radio_group: a single-choice question. The field spec includes an "options" list with the available choices. Return the EXACT option text you want to select. If none of the preset options fit and "Свой вариант" is listed, return "open: <your custom answer>". Example: {"2": "нет"} or {"2": "open: <short answer grounded in the candidate profile>"}
- checkbox: a yes/no binary choice. Return "yes" to check it, "no" to leave it unchecked. Decide based on candidate profile and vacancy context
- If a question is unclear or unanswerable from the profile: return empty string ""
