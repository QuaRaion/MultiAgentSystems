# Документация: AI Technical Interview System
## Система автоматического проведения технических собеседований

**Язык**: Python 3.12+  
**Framework**: Streamlit + LangGraph + GigaChat API

---

## Описание проекта

AI Technical Interview System — это **полностью автоматизированная система проведения технических собеседований**, которая:


---

## Быстрый старт

### 1. Установка зависимостей
```bash
pip install -r requirements.txt
```

### 2. Конфигурация (.env)
```env
GIGACHAT_API_KEY=your_api_key_here
GIGACHAT_SCOPE=GIGACHAT_API_PERS
```

### 3. Запуск
```bash
streamlit run streamlit_app.py
```
---

## Архитектура

```
[Кандидат] → [Observer Agent] → [Interviewer Agent] → [Feedback Agent]
                   ↓                     ↓                    ↓
              JSON анализ       Адаптивные вопросы    Вердикт + Roadmap
```

### Основные компоненты:

1. **Observer Agent** — анализирует ответы кандидата:
   ```json
   {
     "intent": "technical_answer|meta_question|stop|off_topic",
     "answer_quality": "weak|ok|strong",
     "hallucination": true/false,
     "reasoning": "объяснение"
   }
   ```

2. **Interviewer Agent** — задает вопросы, адаптируя сложность по `answer_quality`

3. **Feedback Agent** — генерирует итоговую оценку в формате:
   ```
   ## Вердикт
   Grade: Junior(-) | Рекомендация: No Hire
   
   ## Анализ Hard Skills
   ✅ SQL JOINs | ❌ Window Functions
   
   ## Roadmap
   - Изучить pandas.groupby()
   - Практика LeetCode Medium
   ```

---

## Структура проекта

```
interview-system/
├── interview_chat.py       # Основной Streamlit чат
├── lang_graph.py          # LangGraph workflow (опционально)
├── .env                   # API ключи
├── logs/                  # Автосохранение логов *.json
├── .streamlit/secrets.toml # Альтернатива .env для Streamlit
└── requirements.txt
```
