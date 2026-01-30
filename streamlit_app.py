import streamlit as st

if st.button("⏹️ Завершить интервью"):
    st.session_state.chat_history.append(("system", "Интервью завершено пользователем"))
    st.session_state.interview_state.stop_interview = True
    st.rerun()

from MultiAgentSystems.multi_agent_systems import (
    InterviewState,
    observer_agent,
    interviewer_agent,
    feedback_agent
)

st.set_page_config(page_title="Interview Chat")
st.title("Technical Interview")

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

if "interview_state" not in st.session_state:
    st.session_state.interview_state = InterviewState(
        position="Data Analyst",
        target_grade="Junior",
        experience="Почти год опыта с SQL, Python, Excel"
    )

for role, text in st.session_state.chat_history:
    with st.chat_message(role):
        st.write(text)

user_input = st.chat_input("Введите ответ кандидата")

if user_input:
    st.session_state.chat_history.append(("user", user_input))

    observer_result = observer_agent.invoke(user_input)
    st.session_state.chat_history.append(("observer", observer_result))
    
    if observer_result.get("intent") == "stop":
        st.session_state.chat_history.append(("assistant", "Понял, завершаем интервью. Спасибо!"))
        st.session_state.interview_state.stop_interview = True
        st.rerun()
    
    interviewer_reply = interviewer_agent.invoke(
        interview_state=st.session_state.interview_state,
        observer_result=observer_result,
        history=[{"role": r, "content": t} for r, t in st.session_state.chat_history],
    )
    st.session_state.chat_history.append(("assistant", interviewer_reply))
    st.rerun()

if st.session_state.interview_state.stop_interview:
    st.info("✅ Интервью завершено")    
        
if st.session_state.interview_state.stop_interview and "feedback_shown" not in st.session_state:
        st.session_state.feedback_shown = True
        
        log = {"turns": st.session_state.chat_history, "position": st.session_state.interview_state.position}
        feedback = feedback_agent.invoke(log)
        
        st.session_state.chat_history.append(("system", feedback))
        st.rerun()