import random
import re

from core.services.llm_engine import LLMEngine
from core.services import evaluator


# -------------------------------------------------
# CONFIG
# -------------------------------------------------

SCREENING_TOPICS_COUNT = 5
HR_MIN = 2
HR_MAX = 5

TOTAL_MIN = 15
TOTAL_MAX = 20

llm_engine = LLMEngine()


# =====================================================
# INTENT PATTERNS
# =====================================================

END_PATTERNS = ["end interview", "stop interview", "quit", "exit"]
REPEAT_PATTERNS = ["repeat", "say again", "once again"]
SKIP_PATTERNS = ["skip", "skip the question", "next", "move on", "pass"]


def _contains(text, patterns):
    return bool(text) and any(p in text.lower() for p in patterns)





# =====================================================
# MAIN ENTRY
# =====================================================

def get_next_question(session):


    # ---------------- STORE PREVIOUS ANSWER ----------------
    if session.last_answer and hasattr(session, "last_question"):
        qid = session.last_question.get("id")
        if qid and qid not in session.answers:
            session.answers[qid] = session.last_answer


    # ---------------- INIT SAFETY ----------------

    if getattr(session, "total_questions_asked", None) is None:
        session.total_questions_asked = 0

    if getattr(session, "total_limit", None) is None:
        session.total_limit = random.randint(TOTAL_MIN, TOTAL_MAX)

    # ---------------- GLOBAL HARD STOP ----------------

    HARD_END = [
        "end interview",
        "stop interview",
        "end the interview",
        "stop the interview",
        "end",
        "stop",
        "quit",
        "exit",
    ]

    if _contains(session.last_answer, HARD_END):
            session.phase = "finished"
            session.finished = True
            return _q(
                "end",
                f"Thank you {session.candidate_name or ''}. "
                "The interview has now ended. Have a great day."
            )

    # ---------------- GLOBAL REPEAT ----------------

    if _contains(session.last_answer, REPEAT_PATTERNS):
        if hasattr(session, "last_question"):
            return session.last_question


    # ---------------- INTRO ----------------

    # 1️⃣ Greeting + ask about self

    if session.phase == "intro":

        session.phase = "await_self_intro"

        text = (
            f"Hello and welcome to {session.company}. "
            f"This interview is for the {session.role_label} role. "
            "Now please tell me about yourself."
        )

        return _q("welcome", text)


    # 2️⃣ Capture name from self-intro + ask JMS

    if session.phase == "await_self_intro":

        session.candidate_name = (
            _extract_name(session.last_answer) or session.candidate_name
        )

        session.phase = "ask_JMS"

        return _q(
            "JMS",
            "Can you tell me what you know about JMS?"
        )


    # 3️⃣ Role confirmation

    if session.phase == "ask_JMS":

        session.phase = "role_check"

        return _inc(session, _q(
            "role_confirm",
            f"Thank you {session.candidate_name or 'there'}. "
            f"Do you have knowledge about the {session.role_label} role?"
        ))


    # ---------------- ROLE GATE ----------------

    if session.phase == "role_check":

        if not evaluator.evaluate_role_confirmation(
            session.last_answer
        ):

            session.phase = "finished"
            session.finished = True

            return _q(
                "exit",
                f"Thank you {session.candidate_name}. "
                "Our team will be Contact you. "
                "Have a great day."
            )

        session.phase = "education"


    # ---------------- EDUCATION ----------------

    # 1️⃣ Ask education
    if session.phase == "education":

        session.phase = "await_education_answer"

        return _inc(session, _q(
            "education",
            f"{session.candidate_name}, can you tell me about your education?"
        ))


    # 2️⃣ Accept ANY education answer → move on
    if session.phase == "await_education_answer":

        # store education answer (optional, already stored in answers dict)
        session.phase = "screening_topics"

        return get_next_question(session)



    # =====================================================
    # SCREENING TOPICS
    # =====================================================

    if session.phase == "screening_topics":

        if not hasattr(session, "topics_asked"):
            session.topics_asked = []
            session.current_topic = None
            session.awaiting_experience = False

               # ---- handle answer to familiarity ----
        if session.awaiting_experience:

            session.awaiting_experience = False
            ans = (session.last_answer or "").lower()

            # -------------------------------
            # HARD NEGATIVE FILTER  ✅ NEW
            # -------------------------------
            HARD_NO = [
                "don't know",
                "do not know",
                "no idea",
                "not familiar",
                "never worked",
                "no experience",
                "zero experience",
                "0 experience",
                "not worked",
            ]

            # SKIP / HARD NO → DROP TOPIC
            if (
                any(p in ans for p in SKIP_PATTERNS)
                or any(p in ans for p in HARD_NO)
            ):
                session.current_topic = None

            else:
                positive = evaluator.is_positive(ans)

                if positive:

                    try:
                        text = llm_engine.generate_topic_experience_question(
                            role=session.role_label,
                            topic=session.current_topic,
                        )
                    except Exception:
                        session.current_topic = None
                        return get_next_question(session)

                    return _inc(session, _q(
                        f"exp-{session.current_topic}",
                        text,
                        source="llm",
                    ))

                session.current_topic = None


        # ---- new topic ----
        if len(session.topics_asked) < SCREENING_TOPICS_COUNT:

            topic = llm_engine.pick_next_topic(
                role=session.role_label,
                exclude=session.topics_asked,
            )

            session.topics_asked.append(topic)
            session.current_topic = topic
            session.awaiting_experience = True

            try:
                text = llm_engine.generate_topic_familiarity_question(
                    role=session.role_label,
                    topic=topic,
                )
            except Exception:
                return get_next_question(session)

            return _inc(session, _q(
                f"topic-{topic}",
                text,
                source="llm",
            ))

        session.phase = "hr_llm"


    # =====================================================
    # HR BLOCK
    # =====================================================

    if session.phase == "hr_llm":

        if getattr(session, "llm_hr_count", None) is None:
            session.llm_hr_count = 0

        if getattr(session, "hr_limit", None) is None:
            session.hr_limit = random.randint(HR_MIN, HR_MAX)

        # -------------------------------
        # FIXED FINAL HR QUESTIONS
        # -------------------------------
        if getattr(session, "final_hr_queue", None) is None:
            session.final_hr_queue = [
                # 1️⃣ new
                "What is your current job location?",
                # 2️⃣ new
                "Where is your hometown located?",

                # 3️⃣ existing
                "What is your current notice period, and when would you be available to start if offered the position?",
                # 4️⃣ existing
                "What was your last month in-hand salary?",
                # 5️⃣ existing
                "What are your hobbies or interests outside of work?",

                # 6️⃣ new
                "Are you aware of any AI tools that help you in your daily work?",
                "Are you Open to work from office ?"
            ]

        # -------------------------------
        # LLM HR QUESTIONS
        # -------------------------------
        if session.llm_hr_count < session.hr_limit:

            try:
                text = llm_engine.generate_hr_screening_question(
                    role=session.role_label
                )
            except Exception:
                text = "What motivates you to join this organization?"

            # -------------------------------
            # 🚫 BLOCK NOTICE FROM LLM
            # -------------------------------
            if "notice period" in text.lower():

                # skip LLM notice → go to final HR queue
                session.phase = "final_hr"
                return get_next_question(session)

            session.llm_hr_count += 1

            return _inc(session, _q(
                f"hr-{session.llm_hr_count}",
                text,
                source="llm",
            ))

        session.phase = "final_hr"


    # =====================================================
    # FINAL HR
    # =====================================================

    if session.phase == "final_hr":

        if session.final_hr_queue:

            return _inc(session, _q(
                f"hr-final-{len(session.final_hr_queue)}",
                session.final_hr_queue.pop(0),
                source="system",
            ))

        session.phase = "finished"
        session.finished = True


    # =====================================================
    # END
    # =====================================================

    if session.phase == "finished":

        return _q(
            "end",
            f"Thank you {session.candidate_name}. "
            "Our team will be Contact you Soon. "
            "We appreciate your time."
        )

    raise RuntimeError(f"Unknown phase: {session.phase}")



# =====================================================
# HELPERS
# =====================================================

def _inc(session, q):
    session.total_questions_asked += 1
    session.last_question = q
    return q


def _q(qid, text, source="system"):
    return {"id": qid, "text": text, "source": source}


def _extract_name(answer):

    if not answer:
        return None

    text = answer.lower().strip()

    patterns = [

        # my name is Rahul / my name is Rahul Patel
        r"my name is ([a-zA-Z ]+)",

        # i am Rahul / i am Rahul Patel
        r"i am ([a-zA-Z ]+)",

        # this is Rahul
        r"this is ([a-zA-Z ]+)",

        # myself Rahul / myself Rahul Patel
        r"myself ([a-zA-Z ]+)",

        # name Rahul
        r"name is ([a-zA-Z ]+)",
    ]

    for pat in patterns:
        m = re.search(pat, text)
        if m:
            name = m.group(1).strip()

            # clean trailing words
            name = re.sub(
                r"(here|speaking|sir|maam|madam)$",
                "",
                name,
            ).strip()

            # capitalize properly
            return " ".join(w.capitalize() for w in name.split())

    return None

