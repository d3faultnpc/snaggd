# Fallback-only: QuestionsHandler uses LLM directly (_agent).
# HRMatcher is only called when _agent is None (no LLM_API_KEY).
# hr_questions.md: optional user-written answers bank; returns {} silently if missing.
from typing import Dict
from config import CONFIG

try:
    from core.llm_agent import LLMAgent
    _agent = LLMAgent()
except Exception:
    _agent = None


class HRMatcher:
    """HR question answering: user-written answers first, then LLM, then static fallback."""

    def __init__(self):
        self.questions = self._load_questions()
        print(f"   📋 Loaded {len(self.questions)} HR answers")

    def find_answer(self, question: str) -> str:
        # 1. User-written answer from hr_questions.md (near-exact text match)
        match = self._find_in_bank(question.lower())
        if match:
            return match

        # 2. LLM — handles everything else using candidate profile
        if _agent is not None:
            try:
                return _agent.answer_question(question)
            except Exception:
                pass

        # 3. Static fallback when LLM is unavailable
        return "Happy to discuss further at the interview."

    def _find_in_bank(self, question_lower: str) -> str:
        for q_text, answer in self.questions.items():
            q_lower = q_text.lower()
            if (len(question_lower) > 10 and q_lower in question_lower) or \
               (len(q_lower) > 10 and question_lower in q_lower):
                return answer
        return ""

    def _load_questions(self) -> Dict[str, str]:
        questions_path = CONFIG.data_dir / "hr_questions.md"
        try:
            content = questions_path.read_text(encoding="utf-8")
            questions = {}
            current_q = None
            current_a = []
            for line in content.split("\n"):
                if line.startswith("## "):
                    if current_q:
                        questions[current_q] = "\n".join(current_a).strip()
                    current_q = line[3:].strip()
                    current_a = []
                elif current_q and line.strip() and not line.startswith("tags:"):
                    current_a.append(line.strip())
            if current_q:
                questions[current_q] = "\n".join(current_a).strip()
            return questions
        except Exception as e:
            print(f"   ⚠️ Error loading hr_questions.md: {e}")
            return {}
