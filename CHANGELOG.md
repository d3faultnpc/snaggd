# CHANGELOG — HH Auto Test

> Цель файла: синхронизация между сессиями Claude Code и OpenClaw.  
> Формат: новые записи СВЕРХУ. Каждая запись — что изменено, почему, статус.

---

## 2026-05-21 — Phase 1: LLM stack + adapter pattern + wizard (сессии 2-4)

### Что добавлено

**LLM стек (core/llm_agent.py)**
- `LLMAgent`: OpenRouter gateway, единый клиент для всех AI вызовов
- `generate_cover()` / `score_vacancy()` / `fill_form()` / `answer_question()`
- System prompt: resume_facts.md + job_preferences.md + tone_of_voice.md, кешируется на сессию
- `prompts/`: cover_letter.md, match_scoring.md, form_fill.md
- `form_handlers/questions.py`: batch LLM fill (все поля → один вызов → заполнить)
- `hr_matcher.py`: удалён keyword index, LLM primary, hr_questions.md как опциональный банк

**Adapter pattern (adapters/)**
- `adapters/base.py`: SiteAdapter ABC (6 абстрактных методов)
- `adapters/hh/`: HHAdapter + browser + detector + handlers (git mv, история сохранена)
- `main.py`: тонкий оркестратор — HHAdapter + Logger, вся HH логика в адаптере
- `adapters/hh/adapter.py`: verify() → start() → get_vacancies() → process_vacancy()

**Multi-URL search**
- `data/search_urls.txt`: один URL на строку (вместо одного HH_SEARCH_URL в .env)
- `browser.py`: get_vacancy_urls() итерирует все URL, дедуплицирует
- Поддержка нескольких ролей/направлений резюме в одной сессии

**Onboarding wizard (onboarding/wizard.py)**
- Block D → A → B → C (D первым — устанавливает LLM_API_KEY до парсинга резюме)
- Block A: PDF/DOCX/MD/PNG → LLM → resume_facts.md + ручной ввод как fallback
- Block B: цикл "добавить ещё URL?", пишет search_urls.txt, job_preferences.md
- Block C: tone_of_voice.md (formality, length, language, sample)
- Block D: .env patch (LLM_API_KEY, MODEL, HEADLESS, MAX_VACANCIES)
- `--block a/b/c/d`: запуск одного блока

**Resume parser (onboarding/resume_parser.py)**
- Multimodal: PDF/images → Gemini base64 image_url (нет локального извлечения)
- DOCX → python-docx text, MD/TXT → text mode
- ResumeData dataclass, completeness score (tier1/2/3), HINT/EMPTY маркеры
- json_repair fallback для битого LLM JSON

**Прочее**
- `main.py`: stop_keywords title filter из job_preferences.md
- `sandbox/`: gitignored test environment (DATA_DIR=sandbox/data)
- `venv/`: пересоздан (был артефактом /hh-auto/venv)
- `.env.example`: убран HH_SEARCH_URL, добавлена ссылка на search_urls.txt
- Dead code удалён из llm_cover.py: template system (~100 строк), self.resume_facts

### Статус
✅ Все импорты чистые  
✅ Парсер резюме: 100% completeness на реальном PDF  
✅ ABC enforcement работает (BadAdapter → TypeError)  
⚠️ Интеграционный тест (browser + HH live) не запускался в этих сессиях  
⚠️ stop_companies фильтр требует скрейпинга имени работодателя (не реализован)  
⚠️ HH_Auto → data/ миграция не сделана (workspace_dir legacy ещё жив)

---

## 2026-04-06 (цикл 5) — QuestionsHandler: добавлен submit после заполнения анкеты

### Проблема
Вакансия #9 `Senior Product Owner`: форма с 1 полем (Сопроводительное письмо) классифицирована как
`EMPLOYER_QUESTIONS`. `QuestionsHandler` заполнял поле но не нажимал отправить → статус `questions_filled`
(success=True, но отклик не отправлен). Кнопка `vacancy-response-submit-popup` была видна в снимке 03.

### Что изменено

#### `form_handlers/questions.py`
- `inp.fill()` → `inp.type(text, delay=10)` (триггерит React события, нужно для enabled кнопки)
- Добавлен метод `_submit(page, filled_count, total_fields)`:
  - Пробует `letter_submit` и `popup_submit` (ждёт `:not([disabled])` до 5с)
  - Fallback по тексту кнопки: 'отправить', 'откликнуться', 'далее', 'подтвердить'
  - Успех → `applied`, нет кнопки → `skipped_no_submit`
- Импорт `SELECTORS` добавлен

### Статус
⚠️ Не протестировано — вакансия #9 уже в applied_log, нужна свежая похожая вакансия.

---

## 2026-04-06 (цикл 4) — Фикс таймаута на disabled кнопке + детекция попапа с вопросами

### Проблема
`Chief Product Owner`: попап содержал только `vacancy-response-question` (6 штук) и `add-cover-letter`.
Никакой textarea → кнопка `vacancy-response-submit-popup` навсегда disabled → `click()` висел 30с → `skipped_error`.

### Что изменено
- `hh_modal.py` `_find_nav_button`: добавлена проверка `not btn.is_disabled()` — не возвращаем disabled кнопку
- `form_detector.py`: детекция `vacancy-response-question` в попапе → `has_popup_questions = True`
- `_classify_form` приоритет **0в**: `has_popup_questions` → `EMPLOYER_QUESTIONS` (до HH_MODAL)
- `config.py`: добавлены `popup_questions`, `popup_add_cover`

### Статус
⚠️ Не протестировано.

---

## 2026-04-06 (цикл 3) — Авточтение Сбера: CHAT_INTERFACE детекция до hh_modal

### Проблема (верифицировано через debug 2026-04-06)
Сбер вакансии с автопросмотром — `form-helper-error` виден УЖЕ в `02_after_apply_click`
(ДО попытки отправки), вместе с `vacancy-response-link-view-topic`.
FormDetector находил навигационные ключевые слова раньше и возвращал `hh_modal_step1`.
HHModalHandler заполнял textarea, нажимал "Отправить" — `form-helper-error` исчезал
(текст в поле появился, ошибка валидации ушла), возвращался `applied` — ЛОЖНЫЙ УСПЕХ.

### Что изменено

#### `form_handlers/base.py`
- Добавлен `FormInfo.has_form_error`

#### `form_detector.py`
- Добавлена детекция `form-helper-error` → `info.has_form_error`
- В `_classify_form`: **приоритет 0б** — `has_form_error + has_chat_link` → `CHAT_INTERFACE`
  (раньше HH_MODAL, теперь правильно детектирует авточтение до попытки submit)

#### `config.py`
- Добавлены: `chatik_add_cover` (`chatik-chat-message-applicant-action`)
- Добавлен: `chatik_input` (`chatik-new-message-text`)
- ⚠️ chatik-селекторы НЕ верифицированы на живой модалке

#### `form_handlers/chat.py`
- Переписан с нуля
- Клик по `vacancy-response-link-view-topic` (верифицированный селектор)
- Попытка найти `chatik-chat-message-applicant-action` → клик
- Поиск поля ввода: `chatik-new-message-text` → `contenteditable` → `textarea`
- `type(text, delay=10)` + отправка через кнопку "Отправить" или Enter
- Новый статус: `applied_via_chat`

### Статус
⚠️ Не протестировано — chatik-селекторы требуют живой Сбер-вакансии с авточтением.
Следующая встреча с такой вакансией — ПЕРВЫЙ НАСТОЯЩИЙ ТЕСТ этого флоу.

---

## 2026-04-06 (цикл 2) — TEST_FORM + фикс press_sequentially → type()

### Новый кейс: TEST_FORM (`skipped_unknown` → обработан)
- Работодатель прикрепил тест/вопросы к отклику
- data-qa: `employer-asking-for-test`, `task-body`, `task-question`
- `vacancy-response-link-no-questions` — ссылка «без ответа на вопросы»
- `vacancy-response-letter-toggle` — тогл скрытого cover letter

**Что сделано:**
- Добавлен `FormType.TEST_FORM`
- Добавлен `FormInfo.has_test_form` (детекция по `employer-asking-for-test`)
- Добавлен `TestFormHandler`: кликает no-questions → тогл cover → type() → submit
- Добавлены SELECTORS: `test_form_marker`, `test_no_questions`, `letter_toggle`

### Фикс: `press_sequentially` → `type()`
- `query_selector()` возвращает `ElementHandle`, не `Locator`
- `press_sequentially` — метод Locator, на ElementHandle нет
- Заменено на `ElementHandle.type(text, delay=10)` в `hh_modal.py` и `test_form.py`

### Фикс: ложный success при незаполненном cover
- HH возвращал ошибку «Пожалуйста, введите сопроводительное письмо»
- Код логировал `hh_modal_navigation` (success=True) — неправильно
- Добавлена проверка «введите»/«заполните» в `_check_post_submit_edge_case`
- Теперь возвращает `skipped_no_cover_filled` (success=False)

### Статус
✅ Протестировано — цикл 3: applied ×2, applied_immediate ×1. Попап-фикс работает.

---

## 2026-04-06 — Фикс попап-модала: disabled кнопка + textarea не находилась

### Проблема (из логов)
Три сессии подряд давали `skipped_hh_modal` и `hh_modal_navigation`:
- `skipped_hh_modal` — `HHModalHandler._find_nav_button` возвращал None
- `hh_modal_navigation` — кнопка найдена и нажата, но textarea не заполнена (сопроводительное не отправлено)
- `skipped_no_chat_button` — отдельный edge case, чат-редирект не сработал

### Корневые причины
1. **Popup submit button начинается в состоянии `disabled`** (верифицировано через debug HTML).  
   Кнопка `[data-qa="vacancy-response-submit-popup"]` имеет `disabled=""` пока textarea пустая.  
   `textarea.fill()` не тригерит React-события → кнопка остаётся disabled → клик игнорируется браузером.

2. **Popup textarea не находилась** (до текущего фикса).  
   Селектор `[data-qa="vacancy-response-popup-form-letter-input"]` отсутствовал в `_find_cover_textarea` —  
   это и давало `hh_modal_navigation` (кнопка нажималась без заполнения письма).

### Что изменено

#### `config.py`
- Добавлены селекторы попапа (было в предыдущей сессии, уже в коде):
  - `popup_submit`: `[data-qa="vacancy-response-submit-popup"]`
  - `popup_letter_input`: `[data-qa="vacancy-response-popup-form-letter-input"]`

#### `form_handlers/hh_modal.py`
- `_find_cover_textarea`: добавлен `[data-qa="vacancy-response-popup-form-letter-input"] textarea` первым в списке
- `process`: `textarea.fill()` → `textarea.press_sequentially(cover_letter, delay=10)`  
  Причина: `press_sequentially` эмулирует реальный ввод per-keystroke, триггерит React input/change события
- `_find_nav_button`: добавлено `page.wait_for_selector(selector + ":not([disabled])", timeout=5000)`  
  перед каждой попыткой найти кнопку — ждём пока станет enabled после заполнения

### Статус
⚠️ **Не протестировано** — фикс применён, но новый тестовый прогон не запускался.  
Следующий шаг: `source venv/bin/activate && python main.py --debug --max 3`

### Верифицированные data-qa (из debug снимков 2026-04-05/06)
| Элемент | data-qa | Примечание |
|---------|---------|------------|
| Попап submit | `vacancy-response-submit-popup` | `disabled` изначально |
| Попап textarea | `vacancy-response-popup-form-letter-input` | обязательное |
| Инлайн submit | `vacancy-response-letter-submit` | |
| Инлайн textarea | внутри `vacancy-response-letter-informer` | |
| Мгновенный успех | `vacancy-response-success-standard-notification` | без формы |
| Ошибка формы | `form-helper-error` | "Отклик уже просмотрен" |
| Ссылка чат | `vacancy-response-link-view-topic` | после ошибки |

---

## 2026-04-05 — Рефактор: монолит → модульная архитектура (завершён)

### Что сделано
- Монолит `search.py` (1038 строк) разбит на модули согласно `ARCHITECTURE.md`
- Новая точка входа: `main.py`
- Модули: `browser.py`, `form_detector.py`, `form_handlers/`, `llm_cover.py`, `hr_matcher.py`, `logger.py`, `config.py`
- Debug-режим: `--debug --max N` → скриншоты + HTML + data-qa в `debug_screenshots/`

### Статус
✅ Архитектура работает. Базовые сценарии (applied_immediate) проходят.  
❌ Попап-сценарий (`hh_modal`) — проблемы с disabled кнопкой (см. 2026-04-06).

---

## 2026-04-02 — Попытка browser_inspector (не завершена)

### Что делалось
- Написан `browser_inspector.py` для сбора DOM без риска блокировки
- Интеграция в `main.py` через `--browser_inspector`

### Статус
❌ Не завершено. Проблемы с директориями логов и флагом запуска.  
Заморожено — приоритет у фикса попап-модала.

---

## Точка остановки (2026-04-06, цикл 5)

**Где остановились:** Применён фикс `QuestionsHandler` (submit после заполнения). Цикл 5 не перезапускался — пользователь взял паузу.

**Следующий шаг при возобновлении:**
```
source venv/bin/activate && python main.py --debug --max 3
```
Смотреть на: `questions_filled` → должен стать `applied`. Искать попап-авточтение (красная рамка внутри попапа после клика "Откликнуться").

---

## Стратегический роадмап (озвучен 2026-04-06)

### 1. LLM для сопроводительных писем
Заменить заглушки (`llm_cover.py`) на реальные вызовы LLM.
Уже пора — шаблонный fallback работает, но качество низкое.

### 2. Персонализация MD-файлов через резюме
Пользователь скинет резюме → выцепить keywords → пропитать ими MD-файлы симлинка.
Цель: отточить мэтчинг компаний и вакансий по реальной привлекательности для конкретного человека.
Сейчас связи с реальным профилем мало.

### 3. Стоп-лист
Завести список компаний и позиций, которые точно не интересны.
Скипать их до клика "Откликнуться".

### 4. Аналитика рынка через keyword-парсинг вакансий
Расширить `applied_log.json` (или сделать альтернативу):
- Извлекать ключевики из текста вакансий через Python (без LLM)
- Формировать дашборды: профиль рынка, характеристики вакансий, тренды
- Внешняя ценность продукта — видно что происходит на рынке

### 5. SaaS (дальний горизонт)
Сделать то же самое для других людей, монетизировать.
Два мазка архитектуры:
- Мультитенантность: каждый пользователь — своё резюме, свои MD-файлы, свой стоп-лист
- Браузер запускается удалённо (Playwright в облаке или через собственный агент пользователя)
- Дашборды рынка как отдельная публичная фича (лид-магнит)

---

## Планы по проекту

### P0 — Критичные (необходим тест на живых вакансиях)
| # | Задача | Статус |
|---|--------|--------|
| 1 | `QuestionsHandler`: submit после заполнения (`inp.type` + `_submit`) | Код готов, **не протестирован** |
| 2 | Попап-авточтение: красная рамка + "резюме просмотрено" ВНУТРИ попапа (после клика "Откликнуться") → `_check_post_submit_edge_case` | Код готов, **не встречался живьём** |
| 3 | chatik-селекторы: `chatik-chat-message-applicant-action`, `chatik-new-message-text` | Код готов, **не верифицированы** на живой Сбер-вакансии |

### P1 — Важные
| # | Задача | Статус |
|---|--------|--------|
| 4 | `skipped_hh_modal` (вакансия #1 каждый раз) — выяснить почему кнопка не находится | Не начато |
| 5 | `skipped_no_chat_button` (вакансия #3 каждый раз) — чат-редирект не срабатывает | Не начато |
| 6 | `EMPLOYER_QUESTIONS` из попапа с вопросами (vacancy-response-question) — `QuestionsHandler` не умеет работать с попап-вопросами | Частично: детекция есть, handler не адаптирован |

### P2 — Желательные
| # | Задача | Статус |
|---|--------|--------|
| 7 | LLM cover letter генерация (сейчас шаблонный fallback) | Не начато |
| 8 | `browser_inspector.py` интеграция | Заморожено |

---

## Открытые проблемы (backlog)

| Проблема | Приоритет | Статус |
|----------|-----------|--------|
| `QuestionsHandler` не отправлял форму | 🔴 P0 | Фикс применён, не протестирован |
| Попап-авточтение (красная рамка в попапе) | 🔴 P0 | Код готов, не встречался |
| chatik-селекторы не верифицированы | 🔴 P0 | Требует живой Сбер-вакансии |
| `skipped_hh_modal` вакансия #1 | 🟡 P1 | Не исследовано |
| `skipped_no_chat_button` вакансия #3 | 🟡 P1 | Не исследовано |
| `browser_inspector.py` интеграция | 🟢 P2 | Заморожено |

---

## Как использовать этот файл

**Claude Code / OpenClaw:** При старте новой сессии читай этот файл первым.  
Секция "Точка остановки" — откуда продолжать.  
Секция "Планы по проекту" — полная картина задач.  
Секция "Не протестировано" — первым делом запустить тест.
