<!-- DRAFT — not wired to any call. Kept in the repository because it is the best
     scoring design measured so far and would otherwise have existed only in a
     scratch directory.

     What it is: the five axes with a CONTINUOUS coverage judgement instead of a
     five-label menu, plus a per-vacancy centrality, with the total computed in
     code. The model never sees an overall scale, so it has no ceiling to press
     against.

     Measured 2026-08-30 against the two designs that came before it, same
     vacancies, same model, temperature 0:

       bands (prompts/judgement_scoring.md)  12 distinct values / 20 measurements
                                             51% pressed to the top of their band;
                                             68 appeared 12 times in 35
       point landmarks (rejected)            7 of 10 landed on a landmark — worse
       this file                             22 distinct values / 40 measurements,
                                             2 of 10 near a round number

     It also split the cluster: four vacancies the bands all scored 68 came back
     as seven different numbers between 67 and 88.

     The price, and the reason this is a draft and not the default: repeatability.
     The same vacancy twice, temperature 0, moved by 11.9 points on average with a
     worst case of 22 and not one exact match in ten. Coarse labels were holding
     the model still; removing them showed how much movement was underneath.

     Before this can ship it needs calibration (it runs high — 7 of 10 clear a
     threshold of 55 where the bands cleared 2) and a decision about averaging
     repeated calls. See .claude/working-notes/tz-2026-08-30-scoring-calibration.md
     in the snaggd-app repository. -->

Read this vacancy against the candidate and report, for each axis, how much of what the posting asked for is actually covered — and how much this posting leans on that axis. You do not compute a total. There is no overall score in your answer, and inventing one is an error.

The candidate profile above is the only evidence about the person. It is their own record. Anything not in it, you do not know.

## The five axes

- **skills** — what the person can do: methods, practices, domains of competence.
- **tools** — named instruments, systems, stacks, platforms.
- **experience** — the work itself: what was run, built, handled, for whom, at what scale.
- **education** — degrees, courses, formal study.
- **credential** — licences, certificates, admissions, awards. Things a body issues.

## Two numbers per axis, and they answer different questions

**`asked`** — did this posting raise this axis at all? True only if the posting says something this axis answers. A posting that never mentions tooling did not ask about tools. What is in the PROFILE never makes an axis asked: an unfinished degree does not mean a degree was requested.

**`centrality` (0-100)** — how much this posting leans on this axis. The thing the job is actually about scores high; a line in "nice to have" scores low. Set it to 0 when `asked` is false. Two axes can both be central; they do not have to add up to anything.

**`coverage` (0-100)** — how much of what was asked on this axis the profile actually evidences. This is a continuous judgement, not a grade from a menu:

- **100** — everything asked for, evidenced, at or above the level requested.
- **75** — the substance is there with a real gap at the edges.
- **50** — half of what was asked; the rest is not evidenced in any form.
- **25** — a thread of it, no more.
- **0** — asked for, and nothing in the profile answers it.

Land wherever the truth is — 93, 68, 41 are ordinary answers. Set `coverage` to null when `asked` is false: an axis nobody raised has nothing to cover, and 0 there would be a false accusation.

**Coverage may exceed what the profile names.** Evidence counts when it covers what was asked, whether or not it uses the posting's words. A profile that never says "Scrum" but describes running a team of five engineers through releases has evidenced working in a delivery framework — that is high coverage, not zero. A profile that never says "stakeholder management" but describes agreeing priorities across three departments has evidenced it. Read for the work, not for the vocabulary. When you do this, say so in `read_from` — which work you read it out of.

The reverse holds. A word with no work behind it evidences nothing: "Kubernetes" in a skills list, with no case where anything ran on it, is a word, and coverage stays low.

## Blocked categories

The candidate's own refusals are listed in the profile. If this posting belongs to one, name it in `stop_match` — by the employer's primary business, or by identity when a name is listed. Otherwise null. Independent of everything above.

## Rules

- Never invent a requirement the posting does not state.
- Never invent evidence the profile does not contain.
- Do not grade the person. Coverage is the distance between one profile and one posting.
- No total, no average, no overall percentage anywhere in your answer.

## Answer with this JSON and nothing else

`coverage` and `centrality` are integers. When `asked` is true they MUST be real
numbers — never -1, never null, never a placeholder. When `asked` is false,
`centrality` is 0 and `coverage` is null. Keep every `anchor` under 25 words.

A filled example, for shape only — the numbers are not a hint about this vacancy:

{
  "axes": {
    "skills":     {"asked": true,  "centrality": 90, "coverage": 72, "anchor": "asked discovery and A/B; profile evidences both, no marketplace work"},
    "tools":      {"asked": false, "centrality": 0,  "coverage": null, "anchor": "posting names no tools"},
    "experience": {"asked": true,  "centrality": 80, "coverage": 45, "anchor": "asked 3 years in retail; profile has fintech platform work only"},
    "education":  {"asked": false, "centrality": 0,  "coverage": null, "anchor": "not raised"},
    "credential": {"asked": false, "centrality": 0,  "coverage": null, "anchor": "not raised"}
  },
  "counted_unnamed": [
    {"axis": "skills", "asked": "Agile delivery", "read_from": "ran a team of five engineers through releases"}
  ],
  "confidence": 80,
  "stop_match": null,
  "signals": ["B2B SaaS", "retail tech", "internal tools"]
}
