from typing import Dict, List
from config import CONFIG

try:
    from core.llm_agent import LLMAgent
    _agent = LLMAgent()
except Exception:
    _agent = None

class HRMatcher:
    """Матчер HR-вопросов без использования LLM"""
    
    def __init__(self):
        self.questions = self._load_questions()
        self.keyword_index = self._build_keyword_index()
        self.default_answers = self._build_default_answers()
        
    def find_answer(self, question: str) -> str:
        """Находит лучший ответ на HR вопрос"""
        question_lower = question.lower()
        
        # 1. Точное совпадение по keywords
        best_match = self._find_by_keywords(question_lower)
        if best_match:
            return best_match
        
        # 2. Частичное совпадение текста
        best_match = self._find_by_partial_match(question_lower)
        if best_match:
            return best_match
        
        # 3. LLM fallback — answers from profile context
        if _agent is not None:
            try:
                return _agent.answer_question(question)
            except Exception:
                pass

        # 4. Static fallback if LLM unavailable
        return self._get_default_answer(question_lower)
    
    def _load_questions(self) -> Dict[str, str]:
        """Загружает HR-вопросы из файла"""
        questions_path = CONFIG.workspace_dir / "hr_questions.md"
        try:
            with open(questions_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            questions = {}
            current_q = None
            current_a = []
            
            for line in content.split('\n'):
                if line.startswith('## '):
                    if current_q:
                        questions[current_q] = '\n'.join(current_a).strip()
                    current_q = line[3:].strip()
                    current_a = []
                elif current_q and line.strip() and not line.startswith('tags:'):
                    current_a.append(line.strip())
            
            if current_q:
                questions[current_q] = '\n'.join(current_a).strip()
            
            print(f"   📋 Загружено {len(questions)} HR-ответов")
            return questions
            
        except Exception as e:
            print(f"   ⚠️ Ошибка загрузки hr_questions.md: {e}")
            return {}
    
    def _build_keyword_index(self) -> Dict[str, List[str]]:
        """Строит индекс ключевых слов"""
        keyword_map = {
            'почему вы хотите': ['why_company', 'motivation'],
            'почему вы считаете': ['why_me', 'fit'],
            'почему вы ищете': ['reason_for_change', 'job_change'],
            'расскажите о вашем основном': ['product_experience', 'main_product'],
            'сложной ситуации со стейкхолдерами': ['stakeholder_conflict'],
            'как вы приоритизируете': ['prioritization', 'backlog'],
            'как вы работаете с гипотезами': ['hypotheses', 'experimentation'],
            'какие метрики': ['metrics', 'analytics'],
            'как вы взаимодействуете с разработ': ['dev_collaboration', 'teamwork'],
            'как вы собираете требования': ['requirements', 'analysis'],
            'как вы работаете с пользовательскими сценариями': ['user_scenarios', 'cjm'],
            'как вы работаете с аналитикой': ['analytics', 'product_analysis'],
            'как вы принимаете продуктовые решения': ['product_decision', 'decision_making'],
            'как вы работаете с неопределённостью': ['uncertainty', 'problem_solving'],
            'расскажите о важном запуске': ['product_launch', 'mvp_launch'],
            'с какими пользователями': ['user_types', 'b2b', 'b2c'],
            'какие инструменты': ['tools', 'stack'],
            'какая работа вам наиболее интересна': ['job_interest', 'product_type'],
            'какие ваши сильные': ['strengths', 'soft_skills'],
            'какие ваши слабые': ['weaknesses', 'self_reflection'],
        }
        
        return keyword_map
    
    def _build_default_answers(self) -> Dict[str, str]:
        """Строит категориальные ответы по умолчанию"""
        return {
            'experience': 'У меня более 5 лет опыта в продуктовом менеджменте в финтехе.',
            'motivation': 'Интересна возможность развивать продукт и работать с командой.',
            'tools': 'Работаю с Figma, Jira, Confluence, аналитическими системами.',
            'teamwork': 'Предпочитаю открытое общение и регулярные синхронизации.',
            'generic': 'Готов обсудить детали на собеседовании.'
        }
    
    def _find_by_keywords(self, question_lower: str) -> str:
        """Поиск ответа по ключевым словам"""
        for keywords, tags in self.keyword_index.items():
            if keywords in question_lower:
                # Ищем вопрос с подходящим тегом
                for q_text, answer in self.questions.items():
                    q_lower = q_text.lower()
                    for tag in tags:
                        if tag in q_lower:
                            return answer
        return ""
    
    def _find_by_partial_match(self, question_lower: str) -> str:
        """Поиск по частичному совпадению текста"""
        for q_text, answer in self.questions.items():
            q_lower = q_text.lower()
            
            # Проверяем взаимное вхождение
            if (len(question_lower) > 10 and q_lower in question_lower) or \
               (len(q_lower) > 10 and question_lower in q_lower):
                return answer
        return ""
    
    def _get_default_answer(self, question_lower: str) -> str:
        """Получает ответ по категории вопроса"""
        
        # Категоризация по ключевым словам
        if any(kw in question_lower for kw in ['опыт', 'работал', 'делал', 'проект']):
            return self.default_answers['experience']
        
        elif any(kw in question_lower for kw in ['почему', 'зачем', 'мотивация']):
            return self.default_answers['motivation']
        
        elif any(kw in question_lower for kw in ['инструмент', 'система', 'софт', 'программа']):
            return self.default_answers['tools']
        
        elif any(kw in question_lower for kw in ['команда', 'коллега', 'взаимодейств']):
            return self.default_answers['teamwork']
        
        else:
            return self.default_answers['generic']