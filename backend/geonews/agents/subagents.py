"""Retriever and researcher subagents.

Each runs its own loop on a cheaper model with a narrow toolset and returns a *compressed*
result through a terminal submit_findings tool. The orchestrator only ever sees these summaries,
never raw article text, which keeps its context small no matter how much the subagents read.
"""

from pydantic import BaseModel, Field

from geonews.agents.loop import LocalTool, RunContext, agent_loop
from geonews.agents.prompts import RESEARCHER_SYSTEM, RETRIEVER_SYSTEM


class FoundArticle(BaseModel):
    id: str = Field(description="Article id, e.g. A123")
    why: str = Field(description="One line on why it is relevant")


class RetrieverFindings(BaseModel):
    articles: list[FoundArticle] = Field(max_length=8)
    notes: str = Field("", description="Coverage gaps or search caveats")


class Claim(BaseModel):
    claim: str
    citations: list[str] = Field(description="Article ids that directly state the claim")


class ResearchFindings(BaseModel):
    claims: list[Claim]
    conflicts: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


def _submit(model: type[BaseModel]) -> LocalTool:
    return LocalTool(
        name="submit_findings",
        description="Submit your final findings. Call exactly once, when you are done.",
        input_model=model,
        terminal=True,
    )


async def run_retriever(task: str, ctx: RunContext, max_steps: int = 5) -> dict:
    await ctx.emit("subagent_start", {"agent": "retriever", "task": task})
    result = await agent_loop(
        agent="retriever", task="retrieve", system=RETRIEVER_SYSTEM,
        messages=[{"role": "user", "content": task}],
        tool_names=["search_stories", "stories_near", "geocode"],
        local_tools=[_submit(RetrieverFindings)], ctx=ctx, max_steps=max_steps, max_tokens=4000,
    )
    if result.terminal_input is None:
        out = {"articles": [], "notes": f"retriever stopped ({result.stop}): {result.text[:500]}"}
    else:
        found: RetrieverFindings = result.terminal_input
        # Only pass through ids the retriever actually saw in tool results.
        found.articles = [a for a in found.articles if a.id in ctx.seen_ids]
        out = found.model_dump()
    await ctx.emit("subagent_done", {"agent": "retriever", "n": len(out["articles"])})
    return out


async def run_researcher(question: str, article_ids: list[str], ctx: RunContext,
                         max_steps: int = 6) -> dict:
    await ctx.emit("subagent_start", {"agent": "researcher", "ids": article_ids})
    prompt = f"Question: {question}\n\nArticles to read: {', '.join(article_ids)}"
    result = await agent_loop(
        agent="researcher", task="research", system=RESEARCHER_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        tool_names=["get_article", "get_story_timeline"],
        local_tools=[_submit(ResearchFindings)], ctx=ctx, max_steps=max_steps, max_tokens=6000,
    )
    if result.terminal_input is None:
        out = {"claims": [], "conflicts": [], "gaps": [f"researcher stopped ({result.stop})"]}
    else:
        findings: ResearchFindings = result.terminal_input
        for c in findings.claims:
            c.citations = [i for i in c.citations if i in ctx.seen_ids]
        out = findings.model_dump()
    await ctx.emit("subagent_done", {"agent": "researcher", "n": len(out["claims"])})
    return out
