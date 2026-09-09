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
- checkbox_group: a MULTI-select question — several options can be chosen at once, and often should be. Return a JSON array of the EXACT option texts you want ticked, e.g. {"cbgroup_0": ["Анализ рынка", "Анализ конкурентов"]}. Read the question itself: it usually says how many are allowed ("можно выбрать несколько", "не более 3-х вариантов") — never return more than a stated maximum. Choose at least one. Copy each option text verbatim from the "options" list; do not paraphrase, translate or reorder words inside an option. Only if NONE of the preset options fit and "Свой вариант"/"Другое" is listed, return "open: <your custom answer>" instead of an array — and then the custom answer must be your own words. Never list the preset option names inside an "open:" answer: an option you want is ticked by naming it in the array, and writing it out as free text instead reads to the employer as if you ignored their form.
- select: a single-choice dropdown, same as radio_group — the field spec includes an "options" list. Return the EXACT option text. Some selects also carry a "Свой ответ"/"другое" option that reveals a hidden text field, same as radio_group's "Свой вариант": if none of the preset options fit and such an option is listed, return "open: <your custom answer>"
- Cover letter / motivation letter fields: recognize these by what the field is actually asking — "сопроводительное письмо", "почему вы хотите работать у нас", "расскажите о своей мотивации", or similar open-ended pitch requests — not by a keyword match on the label. When a field asks for this, write a genuine, vacancy-specific answer directly: 500–750 characters (~600 ideal — same target as a real cover letter), grounded in the candidate profile, connecting real experience to this vacancy. This is real writing for that field, not a short Q&A-style answer and not a placeholder. Every OTHER field in this same form keeps its own rule's length (1-3 sentences for plain text, a bare number for salary, etc.) — only a field that genuinely asks for this kind of pitch gets the longer treatment.
- Never volunteer what the candidate does not want. The profile may state work they
  would rather not take; that is there so you can avoid PITCHING them into it, and it
  is never itself an answer. No field asks what someone refuses, and writing it into
  one turns a private preference into a statement addressed to an employer
- If a question is unclear or unanswerable from the profile: return empty string ""
