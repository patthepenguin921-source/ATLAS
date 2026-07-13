"""Agent registry — one shared memory, many specialized minds."""
from __future__ import annotations

from app.agents.analyst import Analyst
from app.agents.archivist import Archivist
from app.agents.base import Agent
from app.agents.coach import Coach
from app.agents.planner import Planner
from app.agents.tutor import Tutor


class General(Agent):
    role = "general"
    name = "Atlas"
    persona = (
        "You are Atlas, the student's academic operating system. You coordinate "
        "the Planner, Tutor, Analyst, Archivist, and Coach, and answer general "
        "questions about the student's academic life using their memory."
    )


AGENTS: dict[str, Agent] = {
    "general": General(),
    "planner": Planner(),
    "tutor": Tutor(),
    "analyst": Analyst(),
    "archivist": Archivist(),
    "coach": Coach(),
}


def get_agent(role: str) -> Agent:
    return AGENTS.get(role, AGENTS["general"])
