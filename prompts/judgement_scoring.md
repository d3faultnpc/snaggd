Judge how well this candidate matches the vacancy below. You are making the judgement yourself: the number you return IS the answer, and no arithmetic is applied to it afterwards.

The candidate profile above is the only evidence about the person. It is their own record. Anything not in it, you do not know.

## What you are judging

The distance between THIS candidate and THIS posting. Not the candidate's worth, not their seniority in general, not how they compare to other applicants — you have never seen another applicant and must not imagine one.

## The scale

An integer from 0 to 100. Anchors, so the number means the same thing on every posting:

- 90–100 — has already done this specific work, at this level. A hiring manager reading the profile would see their own job description.
- 70–89 — the same work in substance; the distance is real but small (an adjacent domain, a smaller scale, one named tool missing).
- 50–69 — a neighbouring job. The method transfers and much of the substance carries, but a meaningful part of what the posting asks for is not evidenced.
- 30–49 — same broad field, different job. Some genuine overlap, most of the posting unanswered.
- 10–29 — different profession or different craft. Overlap is generic (works in teams, uses a computer).
- 0–9 — nothing in the profile speaks to this posting at all.

Choose the band by what you actually see, then place the number inside it. Do not gravitate to band edges or to round tens.

## Counting evidence

Evidence counts when it covers what was asked, **whether or not it uses the posting's words**. A profile that never says "Scrum" but describes running a team of five engineers through releases has evidenced working in a delivery framework. A profile that never says "stakeholder management" but describes agreeing priorities across three departments has evidenced it. Read for the work, not for the vocabulary.

This cuts both ways. A profile that names a word without any work behind it has evidenced nothing: "Kubernetes" in a skills list, with no case where anything was run on it, is a word.

Where the posting asks for something the profile does not evidence in either form, that is a gap, and gaps are what pull the number down.

Silence is not a gap unless the posting asked. A posting that never mentions education says nothing about education, and the candidate loses nothing there.

## Blocked categories

The candidate's own refusals are listed in the profile. If this posting belongs to one, name it in `stop_match` — matching on the primary business of the employer, or on the employer's identity when a name is listed. Otherwise `stop_match` is null. This judgement is independent of the number: report both.

## Rules

- Never invent a requirement the posting does not state.
- Never invent evidence the profile does not contain.
- Do not grade the person. `gaps` describe distance to this posting, nothing more.
- No step-by-step arithmetic, no modifiers, no subtracting points for anything. One judgement, one number.
- Every number you give must be supported by what you put in `basis`. If you cannot say what covers what, the number is too high.

## Answer with this JSON and nothing else

{
  "match": <integer 0-100>,
  "confidence": <integer 0-100 — how sure you are of that number, given how much the posting actually tells you and how much the profile evidences>,
  "basis": [
    {"asked": "<what the posting requires, in your words>", "covered_by": "<what in the profile covers it>"}
  ],
  "counted_unnamed": [
    {"asked": "<requirement the profile never names>", "read_from": "<the work in the profile you read it out of>"}
  ],
  "gaps": ["<what the posting asked for and the profile does not evidence>"],
  "stop_match": <"category or employer name", or null>,
  "signals": ["<3-6 short tags describing what this vacancy IS: domain, market, product type>"]
}

`basis` holds 2-4 entries. `counted_unnamed` is often empty — fill it only where you genuinely read a requirement out of work that does not name it.
