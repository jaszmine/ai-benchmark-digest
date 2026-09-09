from typing import Annotated, Sequence
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class TokenTracker(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    total_tokens: int


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    turn_count: int
    max_turns: int
    telemetry: TokenTracker
    accumulated_findings: list[str]
