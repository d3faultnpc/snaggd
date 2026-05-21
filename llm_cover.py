import json
import hashlib
from typing import Tuple, List, Optional
from pathlib import Path
from config import CONFIG

try:
    from core.llm_agent import LLMAgent
    _agent = LLMAgent()
except Exception as _e:
    _agent = None
    print(f"   ⚠️ LLMAgent не инициализирован: {_e} — используется шаблонный fallback")

class LLMCover:
    """Cover letter generator with caching."""

    def __init__(self):
        self.cache_file = Path(CONFIG.cache_file)
        self.cache = self._load_cache()
        self.templates = self._load_templates()
        self.resume_facts = self._load_resume_facts()
        self.last_score: int = 0
        self.last_matched_skills: list = []
        self.last_gaps: list = []
        
    def generate(self, vacancy_text: str) -> Tuple[str, str, List[str]]:
        """
        Generates a cover letter.
        Returns: (cover_letter, template_name, signals)
        """
        text_for_processing = vacancy_text[:CONFIG.llm_max_input_chars]
        text_hash = self._hash_text(text_for_processing)

        if text_hash in self.cache:
            print("   📋 Использую кэшированное сопроводительное")
            return self.cache[text_hash]

        try:
            result = self._generate_with_llm(text_for_processing)
            print("   🤖 Сгенерировано через LLM")
        except Exception as e:
            print(f"   ⚠️ Ошибка LLM: {e}")
            # Fallback to template generation
            result = self._generate_with_templates(text_for_processing)
            print("   📝 Использован шаблон (fallback)")

        self.cache[text_hash] = result
        self._save_cache()

        return result
    
    def _hash_text(self, text: str) -> str:
        """Creates an MD5 hash for vacancy text (used as cache key)."""
        return hashlib.md5(text.encode('utf-8')).hexdigest()[:16]
    
    def _load_cache(self) -> dict:
        """Loads cache from file."""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                print(f"   📋 Загружен кэш: {len(cache)} записей")
                return cache
        except Exception as e:
            print(f"   ⚠️ Ошибка загрузки кэша: {e}")
        return {}
    
    def _save_cache(self) -> None:
        """Saves cache to file."""
        try:
            # Evict oldest entries when cache exceeds limit (simple FIFO)
            if len(self.cache) > CONFIG.cache_size:
                keys_to_remove = list(self.cache.keys())[:-CONFIG.cache_size]
                for key in keys_to_remove:
                    del self.cache[key]
            
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"   ⚠️ Ошибка сохранения кэша: {e}")
    
    def _load_templates(self) -> dict:
        # TODO: DEAD CODE — only reached when _agent is None (no LLM_API_KEY).
        # cover_templates.md + keyword-based template selection is dead UX.
        # Remove together with _generate_with_templates, _parse_vacancy_signals, _select_template.
        templates_path = CONFIG.workspace_dir / "cover_templates.md"
        try:
            with open(templates_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            templates = {}
            current_name = None
            current_tags = []
            current_text = []
            
            for line in content.split('\n'):
                if line.startswith('## Template:'):
                    if current_name and current_text:
                        templates[current_name] = {
                            'tags': current_tags,
                            'text': '\n'.join(current_text).strip()
                        }
                    current_name = line.split(':')[1].strip()
                    current_tags = []
                    current_text = []
                elif line.startswith('tags:'):
                    current_tags = [t.strip() for t in line.split(':')[1].split(',')]
                elif current_name and line.strip() and not line.startswith('---'):
                    current_text.append(line.strip())
            
            if current_name and current_text:
                templates[current_name] = {
                    'tags': current_tags,
                    'text': '\n'.join(current_text).strip()
                }
            
            print(f"   📝 Загружено {len(templates)} шаблонов")
            return templates
            
        except Exception as e:
            print(f"   ⚠️ Ошибка загрузки шаблонов: {e}")
            return {'default': {'tags': [], 'text': 'Добрый день.\n\nЯ продуктовый менеджер с опытом более 5 лет.\n\nБуду рад обсудить возможности.'}}
    
    def _load_resume_facts(self) -> str:
        # TODO: DEAD CODE — LLMAgent loads profile itself via _build_system_prompt().
        # self.resume_facts is never read after __init__.
        resume_path = CONFIG.workspace_dir / "resume_facts.md"
        try:
            with open(resume_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"   ⚠️ Ошибка загрузки resume_facts.md: {e}")
            return ""
    
    def _generate_with_llm(self, vacancy_text: str) -> Tuple[str, str, List[str]]:
        if _agent is None:
            raise RuntimeError("LLMAgent not available")
        cover = _agent.generate_cover(vacancy_text)
        score_data = _agent.score_vacancy(vacancy_text)
        self.last_score = score_data.get("score", 0)
        self.last_matched_skills = score_data.get("matched_skills", [])
        self.last_gaps = score_data.get("gaps", [])
        signals = score_data.get("signals", [])
        return cover, "llm", signals
    
    def _generate_with_templates(self, vacancy_text: str) -> Tuple[str, str, List[str]]:
        """Generates cover letter via templates."""
        signals = self._parse_vacancy_signals(vacancy_text)
        template_name = self._select_template(signals)

        if template_name in self.templates:
            cover_letter = self.templates[template_name]['text']
        else:
            cover_letter = self.templates.get('default', {}).get('text',
                "Добрый день.\n\nЗаинтересован в данной позиции.\n\nБуду рад обсудить детали.")
            template_name = 'default'

        return cover_letter, template_name, signals

    def _parse_vacancy_signals(self, vacancy_text: str) -> List[str]:
        """Extracts keyword signals from vacancy text."""
        text_lower = vacancy_text.lower()
        signals = []
        
        # Platform keywords
        if any(kw in text_lower for kw in ['платформ', 'platform', 'api', 'sdk', 'интеграц', 'partners']):
            signals.append('platform')
        
        # Admin systems keywords  
        if any(kw in text_lower for kw in ['админ', 'backoffice', 'back-office', 'внутренн', 'internal']):
            signals.append('admin_systems')
        
        # Growth keywords
        if any(kw in text_lower for kw in ['рост', 'конверс', 'a/b', 'ab тест', 'маркетинг', 'acquisition']):
            signals.append('growth')
        
        # B2B keywords
        if any(kw in text_lower for kw in ['b2b', 'партнёры', 'корпоратив', 'клиенты']):
            signals.append('b2b')
        
        # B2C keywords
        if any(kw in text_lower for kw in ['b2c', 'пользовател', 'клиент', 'покупател', 'витрина']):
            signals.append('b2c')
        
        return signals
    
    def _select_template(self, signals: List[str]) -> str:
        """Selects the best-matching template by signals."""
        best_template = 'default'
        best_score = 0
        
        for name, template in self.templates.items():
            tags = template.get('tags', [])
            score = sum(1 for s in signals if s in tags or any(s in t for t in tags))
            
            if score > best_score:
                best_score = score
                best_template = name
        
        return best_template