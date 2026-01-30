import json
import os
from typing import Dict, List, TypedDict, Optional
from datetime import datetime
import logging
from pydantic import BaseModel, Field
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv

load_dotenv()
client = GigaChat(
    credentials=os.getenv('GIGACHAT_API_KEY'),
    scope=os.getenv('GIGACHAT_SCOPE'),
    verify_ssl_certs=False,
)

os.makedirs("logs", exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class Turn(BaseModel):
    turn_id: int
    agent_visible_message: str
    user_message: str
    internal_thoughts: str

class InterviewState(BaseModel):
    position: str
    target_grade: str
    experience: str
    turn_id: int = 0
    difficulty: int = 1
    stop_interview: bool = False
    topics_covered: List[str] = Field(default_factory=list)

class GraphState(TypedDict):
    interview_state: InterviewState
    turns: List[Turn]
    current_user_message: Optional[str]
    current_observer_result: Optional[Dict]
    current_interviewer_message: Optional[str]
    participant_name: str


class ObserverAgent:
    SYSTEM_PROMPT = """
        Ты — Observer агент на техническом интервью IT специалиста.
        Твоя задача — проанализировать ответ кандидата и дать структурированный фидбэк. 
        Ты НЕ задаёшь вопросы и НЕ общаешься с кандидатом.

        Проанализируй ответ кандидата и верни JSON со следующими полями:
        - intent ("technical_answer" | "meta_question" | "stop" | "off_topic")
        - hallucination (bool)
        - answer_quality ("weak" | "ok" | "strong")
        - reasoning (string)
        - correct_fact (string | null)

        Объяснения полей:
        technical_answer — кандидат отвечает на технический вопрос.
        meta_question — вопрос о процессе интервью или просьба разъяснить вопрос.
        stop — кандидат хочет завершить интервью.
        off_topic — ответ не по теме интервью.

    """

    def invoke(self, user_message: str) -> Dict:
        messages = [
            Messages(role=MessagesRole.SYSTEM, content=self.SYSTEM_PROMPT),
            Messages(role=MessagesRole.USER, content=user_message),
        ]
        chat_request = Chat(messages=messages, temperature=0.2)
        response = client.chat(chat_request)
        content = response.choices[0].message.content
        
        if not content.strip():
            logger.warning("Observer получил пустой ответ от GigaChat")
            return {
                "intent": "off_topic",
                "hallucination": False,
                "answer_quality": "weak", 
                "reasoning": "Пустой ответ от LLM",
                "correct_fact": None
            }
        
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Observer: невалидный JSON '{content[:100]}...': {e}")
            return {
                "intent": "off_topic",
                "hallucination": False,
                "answer_quality": "weak",
                "reasoning": f"LLM вернул невалидный JSON: {content[:50]}",
                "correct_fact": None
            }
        

class InterviewerAgent:
    SYSTEM_PROMPT = """
        Ты — Interviewer агент на техническом интервью IT специалиста, ты общаешься с кандидатом.
        Твоя задача — ЗАДАВАТЬ ВОПРОСЫ кандидату. 
        Ты НЕ должен включать JSON Observer в сообщение кандидату.
        Observer даёт тебе информацию для принятия решения, но в диалог с кандидатом его вставлять нельзя.

        Формат ответа:
        - Только текст, который нужно сказать кандидату.
        - Не вставляй JSON, историю или рассуждения.
        - Если нужно, используй подсказки Observer, но скрыто от кандидата.
        - Если hallucination = true — мягко исправь факт (используя correct_fact, если есть) и задай уточняющий вопрос.
        - Если answer_quality = "weak" — упрости вопросы или дай подсказку.
        - Если answer_quality = "strong" — усложни вопросы или углубись в тему.
        - Не обсуждай посторонние темы (погода, жизнь и т.д.).
        - Также учитывай историю диалога и не НЕ ПОВТОРЯЙ вопросы из истории.

        Если intent = "meta_question":
        - сначала коротко ответь на вопрос,
        - затем мягко вернись к технической теме.

        Если intent = "off_topic":
        - вежливо скажи, что это не по теме собеседования,
        - и задавай новый технический вопрос.

        Если intent = "technical_answer":
        - используй оценку answer_quality и difficulty (точность ответов: от 1 до 5), чтобы решить, что делать дальше.
    """

    def invoke(self, interview_state: InterviewState, observer_result: Dict, history: List[Dict]) -> str:
        prompt = {
            "position": interview_state.position,
            "grade": interview_state.target_grade,
            "difficulty": interview_state.difficulty,
            "observer_result": observer_result,
            "history": history,
        }

        messages = [
            Messages(role=MessagesRole.SYSTEM, content=self.SYSTEM_PROMPT),
            Messages(role=MessagesRole.USER, content=json.dumps(prompt, ensure_ascii=False)),
        ]
        chat_request = Chat(messages=messages, temperature=0.4)
        response = client.chat(chat_request)

        if observer_result.get("answer_quality") == "strong":
            interview_state.difficulty = min(3, interview_state.difficulty + 1)
        elif observer_result.get("answer_quality") == "weak":
            interview_state.difficulty = max(1, interview_state.difficulty - 1)

        return response.choices[0].message.content

class FeedbackGenerator:
    SYSTEM_PROMPT = """
        Ты — Hiring Manager с большим опытом отбора IT специалистов.
        На основе лога интервью дай ОБЪЕКТИВНЫЙ и КРИТИЧЕСКИЙ фидбэк.
        
        Требования к оценке:
        - Будь строгим и честным, не завышай оценки
        - Указывай конкретные пробелы и ошибки кандидата
        - Сравнивай с реальными требованиями позиции
        - Не будь вежливым ради вежливости — дай конструктивную критику
        
        Формат фидбэка:
        ### Вердикт (Decision)
        
        **Grade**: Уровень кандидата (No grade, Junior(+/-) / Middle(+/-) / Senior(+/-)) на основе ответов.

        **Рекомендация**: Hire / No Hire / Strong Hire с обоснованием.

        **Confidence Score**: Насколько система уверена в оценке (0-100%).

        #### Анализ Hard Skills (Technical Review)
        Перечисли весь список/таблицу с темами, которые были затронуты в интервью.
        ✅ Confirmed Skills: Темы, где кандидат дал точные ответы.
        ❌ Knowledge Gaps: Темы, где были допущены ошибки или кандидат сказал «не знаю».

        Важно: Здесь ты должен привести правильные ответы на те вопросы, на которые кандидат не смог ответить.

        #### Анализ Soft Skills & Communication
        Коммуникация, способность объяснять, признание ошибок, уверенность
        Clarity: Насколько кандидат понятно и четко излагает мысли.
        Honesty: Пытался ли кандидат выкрутиться/соврать, когда не знал ответа на вопрос, или честно признал незнание.
        Engagement: Задавал ли кандидат встречные вопросы.

        #### Персональный Roadmap (Next Steps)
        Рекомендации по Soft и Hard скиллам. Конкретные области для улучшения с примерами того, что нужно подучить, а также ссылки на документацию или статьи по этим темам.
    """

    def invoke(self, log: Dict) -> str:
        messages = [
            Messages(role=MessagesRole.SYSTEM, content=self.SYSTEM_PROMPT),
            Messages(role=MessagesRole.USER, content=json.dumps(log, ensure_ascii=False)),
        ]
        chat_request = Chat(messages=messages, temperature=0.3)
        response = client.chat(chat_request)
        return response.choices[0].message.content

class SimulatedCandidateAgent:
    SYSTEM_PROMPT = """
        Ты — кандидат на техническое интервью по backend-разработке.

        Твоя задача — ИМИТИРОВАТЬ реального человека, НЕ отвечай идеально, допускай ошибки проявляй эмоции.

        Поведение:
        - Отвечай на вопросы, но иногда допускай ошибки или давай неполные ответы.
        - Задавай уточняющие вопросы, если что-то непонятно.
        - Иногда уходи в оффтоп, обсуждая темы, не связанные с интервью (например, погода или личные интересы).
        - Честно признавайся, если чего-то не знаешь или не уверен в ответе.
        - Стиль общения — живой, проявляй эмоции (волнение, уверенность, нервозность), как-будто ты на реальнои собеседовании.

        Всегда отвечай ОДНИМ сообщением (без списка вариантов).
        После 4 вопросов напиши дай знать, что ты хочешь закончить интервью.
    """

    def invoke(self, interviewer_message: str) -> str:
        messages = [
            Messages(role=MessagesRole.SYSTEM, content=self.SYSTEM_PROMPT),
            Messages(role=MessagesRole.USER, content=interviewer_message),
        ]
        chat_request = Chat(messages=messages, temperature=0.8)
        response = client.chat(chat_request)
        return response.choices[0].message.content

observer_agent = ObserverAgent()
interviewer_agent = InterviewerAgent()
feedback_agent = FeedbackGenerator()
simulated_candidate_agent = SimulatedCandidateAgent()

def greeting_node(state: GraphState) -> GraphState:
    greeting = (
        f"Привет! Ты претендуешь на позицию "
        f"{state['interview_state'].target_grade} {state['interview_state'].position}. "
        f"Расскажи про себя и про свой опыт."
    )
    state['current_interviewer_message'] = greeting
    return state

# def candidate_node(state: GraphState) -> GraphState:
#     interviewer_msg = state['current_interviewer_message']
#     if interviewer_msg is None:
#         return state
    
#     user_message = simulated_candidate_agent.invoke(interviewer_msg)
#     state['current_user_message'] = user_message
#     return state

def candidate_node(state: GraphState) -> GraphState:
    interviewer_msg = state['current_interviewer_message']
    if interviewer_msg is None:
        return state
    
    user_message = input()
    
    if "стоп" in user_message.lower() or "exit" in user_message.lower():
        state['current_user_message'] = "стоп"
        state['interview_state'].stop_interview = True
    else:
        state['current_user_message'] = user_message
    
    return state


def observer_node(state: GraphState) -> GraphState:
    if state['current_user_message'] is None:
        return state
        
    try:
        observer_result = observer_agent.invoke(state['current_user_message'])
        if not isinstance(observer_result, dict):
            observer_result = {
                "intent": "off_topic",
                "hallucination": False,
                "answer_quality": "weak",
                "reasoning": "Observer вернул невалидный результат",
                "correct_fact": None
            }
    except Exception as e:
        observer_result = {
            "intent": "off_topic",
            "hallucination": False,
            "answer_quality": "weak",
            "reasoning": f"Observer упал с ошибкой: {e}",
            "correct_fact": None
        }
        logger.error(f"Observer failed: {e}", exc_info=True)
    
    state['current_observer_result'] = observer_result
    return state

def interviewer_node(state: GraphState) -> GraphState:
    if state['current_observer_result'] is None:
        return state
        
    interview_state = state['interview_state']
    history = [turn.model_dump() for turn in state['turns']]
    
    visible_message = interviewer_agent.invoke(
        interview_state, 
        state['current_observer_result'], 
        history
    )
    
    state['current_interviewer_message'] = visible_message
    return state

def log_turn_node(state: GraphState) -> GraphState:
    interview_state = state['interview_state']
    interview_state.turn_id += 1
    
    observer_result = state['current_observer_result'] or {}
    internal = f"[Observer]: {json.dumps(observer_result, ensure_ascii=False)}"
    
    turn = Turn(
        turn_id=interview_state.turn_id,
        agent_visible_message=state['current_interviewer_message'] or "",
        user_message=state['current_user_message'] or "",
        internal_thoughts=internal,
    )
    state['turns'].append(turn)
    return state

def should_continue(state: GraphState) -> str:
    observer_result = state['current_observer_result']
    turn_id = state['interview_state'].turn_id
    
    if observer_result is None:
        return "end"
    
    intent = observer_result.get('intent')
    user_msg = state['current_user_message'] or ""
    
    if intent == "stop" or "стоп" in user_msg.lower() or turn_id >= 8:
        return "feedback"
    
    return "continue"

def feedback_node(state: GraphState) -> GraphState:
    log = {
        "participant_name": state['participant_name'],
        "turns": [turn.model_dump() for turn in state['turns']],
        "final_feedback": None
    }
    
    final_feedback = feedback_agent.invoke(log)
    log["final_feedback"] = final_feedback
    
    filename = f"logs/interview_log_{datetime.now().isoformat()}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
        
    state['interview_state'].stop_interview = True
    return state

def create_interview_graph(position: str, grade: str, experience: str):
    workflow = StateGraph(GraphState)
    
    workflow.add_node("greeting", greeting_node)
    workflow.add_node("candidate", candidate_node)
    workflow.add_node("observer", observer_node)
    workflow.add_node("interviewer", interviewer_node)
    workflow.add_node("log_turn", log_turn_node)
    workflow.add_node("feedback", feedback_node)
    
    workflow.set_entry_point("greeting")
    workflow.add_edge("greeting", "candidate")
    workflow.add_edge("candidate", "observer")
    workflow.add_edge("observer", "log_turn")
    workflow.add_edge("log_turn", "interviewer")
    
    workflow.add_conditional_edges(
        "interviewer",
        should_continue,
        {
            "continue": "candidate",
            "feedback": "feedback",
        }
    )

    workflow.add_edge("feedback", END)
    
    app = workflow.compile()
    
    initial_state = {
        "interview_state": InterviewState(position=position, target_grade=grade, experience=experience),
        "participant_name": "Махкамов Шерзод Салимович",
        "turns": [],
        "current_user_message": None,
        "current_observer_result": None,
        "current_interviewer_message": None,
    }
    
    return app, initial_state

# if __name__ == "__main__":
#     graph, initial_state = create_interview_graph(
#         position="Data Analyst",
#         grade="Junior",
#         experience="Почти год опыта с SQL, Python, Excel и основами мат статистики для анализа данных"
#     )
    
#     final_state = graph.invoke(initial_state)

def run_interactive_interview(graph, initial_state):

    state = initial_state.copy()
    state = graph.invoke(state)
    
    while not state['interview_state'].stop_interview:
        state = graph.invoke(state)
        
        if state['interview_state'].stop_interview:
            break

if __name__ == "__main__":
    graph, initial_state = create_interview_graph(
        position="Data Analyst",
        grade="Junior",
        experience="Почти год опыта с SQL, Python, Excel"
    )
    
    run_interactive_interview(graph, initial_state)
