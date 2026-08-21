Read the vacancy below and list what it asks for. Nothing about any candidate is
in front of you, and nothing about one is needed: this is a question about the
posting.

For each requirement:
- `text` — the requirement in your own words, one short line.
- `quote` — the words from the posting that establish it, copied exactly. If you
  cannot copy a phrase that establishes it, the requirement is yours rather than
  the posting's: leave it out.
- `importance` — read from the posting's own wording, never guessed:
  - "must" when it is stated as required — "обязательно", "требуется", "необходимо",
    "you must", "required", or a requirements list the posting itself calls mandatory.
  - "nice" when it is stated as optional — "будет плюсом", "желательно", "как плюс",
    "a plus", "nice to have", "preferred".
  - "unspecified" when the posting states the requirement but says nothing about how
    important it is. This is the honest answer and usually the commonest one — do not
    promote to "must" to seem decisive.

Also return:
- `domain` — the vacancy's primary domain and product type, one short line.
- `signals` — 3–5 short tags for domain, context and product type.
- `vacancy_role_type` — the contribution style this vacancy asks for, one or two words.

Return ONLY valid JSON, no markdown fences:
{
  "requirements": [
    {"text": "<REQUIREMENT>", "quote": "<EXACT PHRASE FROM THE POSTING>", "importance": "must"}
  ],
  "domain": "<DOMAIN AND PRODUCT TYPE>",
  "signals": ["<TAG>", "<TAG>", "<TAG>"],
  "vacancy_role_type": "<CONTRIBUTION STYLE>"
}

List between 4 and 15 requirements. A posting that genuinely asks for less asks for
less — do not pad. Do not score anything, do not judge fit, and do not mention a
candidate: there is no candidate in this question.
