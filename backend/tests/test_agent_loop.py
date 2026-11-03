"""Agent loop behavior against a scripted fake Claude client (no network, no DB)."""

from types import SimpleNamespace

from anthropic.types.beta import BetaMessage, BetaTextBlock, BetaToolUseBlock, BetaUsage
from pydantic import BaseModel

from geonews.agents.loop import LocalTool, RunContext, agent_loop
from geonews.llm import router as router_mod


def _msg(content, stop):
    return BetaMessage.model_construct(
        id="m", type="message", role="assistant", model="claude-haiku-5-5", content=content,
        stop_reason=stop, usage=BetaUsage.model_construct(
            input_tokens=100, output_tokens=20, cache_read_input_tokens=0,
            cache_creation_input_tokens=0),
    )


def _tool(id_, name, inp):
    return BetaToolUseBlock.model_construct(type="tool_use", id=id_, name=name, input=inp)


def _text(t):
    return BetaTextBlock.model_construct(type="text", text=t)


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **params):
        # Snapshot the message list: the loop keeps appending to the same list.
        self.requests.append({**params, "messages": list(params["messages"])})
        return self.script.pop(0)


def install(script):
    async def sink(_):
        pass

    client = FakeClient(script)
    router_mod._router = router_mod.LLMRouter(client=client, sink=sink)
    return client


class EchoIn(BaseModel):
    text: str


class SubmitIn(BaseModel):
    answer: str


async def echo(args: EchoIn):
    return {"echo": args.text, "id": "A42"}


def tools():
    return [LocalTool("echo", "echo", EchoIn, echo),
            LocalTool("submit", "submit", SubmitIn, terminal=True)]


async def test_parallel_tool_results_in_one_message_then_end():
    client = install([
        _msg([_text("checking"), _tool("t1", "echo", {"text": "a"}), _tool("t2", "echo", {"text": "b"})], "tool_use"),
        _msg([_text("Done [A42].")], "end_turn"),
    ])
    ctx = RunContext()
    res = await agent_loop(agent="test", task="research", system="s",
                           messages=[{"role": "user", "content": "q"}], tool_names=[],
                           local_tools=tools(), ctx=ctx, max_steps=5)
    assert res.stop == "end_turn" and res.text == "Done [A42]."
    results_msg = client.requests[1]["messages"][-1]
    assert results_msg["role"] == "user"
    assert [r["tool_use_id"] for r in results_msg["content"]] == ["t1", "t2"]
    assert "A42" in ctx.seen_ids
    assert ctx.tokens_used == 240
    # Tools prefix is identical across turns (prompt-cache friendly) and history is append-only.
    assert client.requests[0]["tools"] == client.requests[1]["tools"]
    assert client.requests[1]["messages"][:1] == client.requests[0]["messages"]


async def test_invalid_input_returns_error_result():
    client = install([
        _msg([_tool("t1", "echo", {"wrong": 1})], "tool_use"),
        _msg([_text("ok")], "end_turn"),
    ])
    await agent_loop(agent="test", task="research", system="s",
                     messages=[{"role": "user", "content": "q"}], tool_names=[],
                     local_tools=tools(), ctx=RunContext(), max_steps=5)
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "INVALID_INPUT" in result["content"]


async def test_terminal_tool_ends_loop_with_validated_input():
    install([_msg([_tool("t1", "submit", {"answer": "42"})], "tool_use")])
    res = await agent_loop(agent="test", task="research", system="s",
                           messages=[{"role": "user", "content": "q"}], tool_names=[],
                           local_tools=tools(), ctx=RunContext(), max_steps=5)
    assert res.stop == "terminal" and res.terminal_input.answer == "42"


async def test_refusal_and_budget_stop():
    install([_msg([], "refusal")])
    res = await agent_loop(agent="test", task="research", system="s",
                           messages=[{"role": "user", "content": "q"}], tool_names=[],
                           local_tools=tools(), ctx=RunContext(), max_steps=5)
    assert res.stop == "refusal"

    install([_msg([_tool("t1", "echo", {"text": "a"})], "tool_use")] * 3)
    res = await agent_loop(agent="test", task="research", system="s",
                           messages=[{"role": "user", "content": "q"}], tool_names=[],
                           local_tools=tools(), ctx=RunContext(token_budget=100), max_steps=5)
    assert res.stop == "budget" and res.steps == 1
