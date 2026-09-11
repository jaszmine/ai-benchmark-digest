import asyncio
import re

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import get_settings
from src.track_a.pipeline import get_llm
from src.track_b.state import AgentState, TokenTracker
from src.track_b.tools import lean_web_search

settings = get_settings()

RESEARCHER_PROMPT = """You are an autonomous AI research scientist exploring the frontier of artificial intelligence.

Your objective is to discover and verify at least 6 to 8 candidate developments so that exactly the TOP 5 most significant AI advancements, releases, or research discoveries from the past week (or past month if highly impactful) can be synthesized.

Guidelines:
1. Maintain an open, unbiased lens across the entire field — novel architectures, agent frameworks, frontier model releases, compute/hardware advances, open weights, and scientific applications.
2. Formulate your own natural, diverse search queries. Do not include calendar years or hardcoded dates; search handles temporal indexing.
3. CRITICAL REQUIREMENT: You must inspect search results until you have identified at least 6 distinct candidate stories with valid canonical HTTP/HTTPS links. Do not stop after finding only 3 or 4 stories.
4. When you have sufficient verified findings, complete your analysis with full links and source attributions.
"""

tools = [lean_web_search]
tool_node = ToolNode(tools)


def get_agent_llm():
    llm = get_llm()
    return llm.bind_tools(tools)


def update_token_metrics(current: TokenTracker, usage: dict) -> TokenTracker:
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    reasoning = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
    total = usage.get("total_tokens", prompt + completion)

    return {
        "prompt_tokens": current["prompt_tokens"] + prompt,
        "completion_tokens": current["completion_tokens"] + completion,
        "reasoning_tokens": current["reasoning_tokens"] + reasoning,
        "total_tokens": current["total_tokens"] + total,
    }


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=3, min=4, max=30),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def invoke_agent_with_retry(llm, messages):
    return await llm.ainvoke(messages)


async def reasoner_node(state: AgentState) -> dict:
    llm = get_agent_llm()
    messages = state["messages"]

    if not any(isinstance(m, SystemMessage) for m in messages):
        messages = [SystemMessage(content=RESEARCHER_PROMPT)] + list(messages)

    response = await invoke_agent_with_retry(llm, messages)

    usage = getattr(response, "response_metadata", {}).get("token_usage", {})
    updated_telemetry = update_token_metrics(state["telemetry"], usage)

    new_turn_count = state["turn_count"] + 1

    return {
        "messages": [response],
        "turn_count": new_turn_count,
        "telemetry": updated_telemetry,
    }


async def tool_runner_node(state: AgentState) -> dict:
    if settings.inter_turn_sleep_seconds > 0:
        await asyncio.sleep(settings.inter_turn_sleep_seconds)

    result = await tool_node.ainvoke(state)

    findings = list(state.get("accumulated_findings", []))
    for msg in result.get("messages", []):
        if isinstance(msg, ToolMessage) and msg.content:
            findings.append(msg.content)

    return {
        "messages": result.get("messages", []),
        "accumulated_findings": findings,
    }


def count_unique_links(findings: list[str]) -> int:
    """Helper to detect how many unique URLs have been collected in the findings so far."""
    urls = set()
    for text in findings:
        matches = re.findall(r'https?://[^\s)\]"\'>]+', text)
        for u in matches:
            if not any(noise in u for noise in ["bing.com", "google.com", "api."]):
                urls.add(u)
    return len(urls)


def should_continue(state: AgentState) -> str:
    messages = state["messages"]
    last_message = messages[-1]

    # Hard ceiling on turns
    if state["turn_count"] >= state["max_turns"]:
        return "end"

    # If the LLM requested a tool call, run it
    if isinstance(last_message, AIMessage) and getattr(last_message, "tool_calls", None):
        return "tools"

    # If the LLM tried to terminate early but found fewer than 5 unique links,
    # and has budget left, keep searching
    accumulated = state.get("accumulated_findings", [])
    unique_links = count_unique_links(accumulated)
    if unique_links < 5 and state["turn_count"] < state["max_turns"]:
        return "tools"

    return "end"


def build_research_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("reasoner", reasoner_node)
    workflow.add_node("tools", tool_runner_node)

    workflow.set_entry_point("reasoner")

    workflow.add_conditional_edges(
        "reasoner",
        should_continue,
        {
            "tools": "tools",
            "end": END,
        },
    )
    workflow.add_edge("tools", "reasoner")

    return workflow.compile()