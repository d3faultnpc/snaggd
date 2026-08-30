"""The letter is written by an advocate. It does not get told how convinced we are.

`Match score: N/100` used to be the first line of the hint the cover call received,
next to the observations. That is the same shape as the system preamble that had to be
split per reader: one prompt carrying both the case for the candidate and our doubt
about them. Observations stay — what this vacancy is (signals) and what the two
documents genuinely share (matched_skills). The verdict does not.

Not measurable, and said out loud: cover quality in this repo has one metric under
four names and no labelled truth. This is argued, not proven — the test exists so the
argument cannot be undone by accident.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.llm_agent import LLMAgent  # noqa: E402

failures = []


def check(label, condition):
    print(("  ✅ " if condition else "  ❌ ") + label)
    if not condition:
        failures.append(label)


def run():
    agent = LLMAgent.__new__(LLMAgent)
    hint = agent._build_match_hint({
        "score": 42,
        "signals": ["fintech", "payments"],
        "matched_skills": ["KYC/AML"],
        "vacancy_role_type": "builder",
    })

    print("\nWhat the writer is given")
    check("no score, however it is spelled", "42" not in hint and "Match score" not in hint)
    check("the signals survive — they say what this vacancy is", "fintech" in hint)
    check("the shared skills survive — they are observation, not verdict",
          "KYC/AML" in hint)

    print("\nAnd a score cannot sneak back in through the caller")
    check("passing one changes nothing",
          agent._build_match_hint({"score": 99, "signals": ["a"]})
          == agent._build_match_hint({"signals": ["a"]}))

    # The same line, drawn one axis lower. An anchor is what the posting asked
    # about; a grade is how convinced we are that the person answers it. The
    # first is the writer's business and the second is the verdict under another
    # name — which is exactly how a verdict comes back once the score itself is
    # blocked at the door.
    print("\nAxis anchors travel; the grades beside them do not")
    axed = agent._build_match_hint({
        "signals": ["fintech"],
        "axes": {
            "skills": {"grade": "weak", "anchor": "product discovery, Agile"},
            "education": {"grade": "neutral", "anchor": "образование не требуется"},
        },
        "axes_in_play": ["skills"],
    })
    check("the anchor of an axis in play reaches the writer",
          "product discovery, Agile" in axed)
    check("its grade does not", "weak" not in axed)
    check("an axis the posting never raised stays out",
          "образование не требуется" not in axed)
    check("no axes, no section", "posting actually asked about"
          not in agent._build_match_hint({"signals": ["a"]}))

    print()
    print(f"{'❌ ' + str(len(failures)) + ' failed' if failures else '✅ all passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
