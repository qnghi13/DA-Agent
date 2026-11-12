"""Check a local model server before an eval run spends GPU hours on it. Example:

VLLM_BASE_URL=http://localhost:8000/v1 uv run python -m eval.gpu.preflight \
    --routes vllm:Qwen/Qwen3-14B

Three checks, all on synthetic prompts with no shop data:
- tool_call: the model calls an offered tool with JSON arguments, so the server's tool parser
  matches the model's format. Without it every agent turn fails.
- repeatable: one prompt gives the same answer alone and amid a busy batch. The challenge re-runs
  the image and the two scores must agree within 0.03, so output must not depend on the batch.
- throughput: output tokens per second with several requests at once, to size --workers.

Exits 1 when tool_call or repeatable fails.
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from da_agent.llm.client import CostLedger, LLMClient
from da_agent.llm.factory import build_routes
from da_agent.llm.types import LLMError, LLMRequest, Message, ToolSpec

SQL_TOOL = ToolSpec(
    name="run_sql",
    description="Run one read-only SQL query on the dataset and return its rows.",
    parameters={
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "One SELECT statement."}},
        "required": ["sql"],
    },
)

TOOL_PROMPT = (
    "The dataset has a table orders(order_id, order_date, amount). "
    "How many orders are there? Use the run_sql tool."
)

REPEAT_PROMPT = (
    "List five different ways a small shop could raise its average order value. "
    "One short sentence each, numbered."
)

# Distinct prompts that keep the server busy while the repeated one runs.
BUSY_PROMPTS = [
    "Explain what a database index is, in four sentences.",
    "Write a short SQL query that counts rows per day in a table named sales.",
    "Give three tips for keeping a spreadsheet of stock tidy.",
    "Describe the difference between revenue and profit in three sentences.",
    "Summarise why dates should be stored in ISO format, in two sentences.",
]


def _ask(llm: LLMClient, prompt: str, run_id: str, tools: list[ToolSpec] | None = None) -> Any:
    request = LLMRequest(
        messages=[Message(role="user", content=prompt)],
        tools=tools or [],
        max_output_tokens=512,
    )
    return llm.complete(request, run_id=run_id)


def check_tool_call(llm: LLMClient) -> dict[str, Any]:
    try:
        response = _ask(llm, TOOL_PROMPT, "preflight-tool", tools=[SQL_TOOL])
    except LLMError as error:
        return {"ok": False, "detail": f"request failed: {error}"}
    calls = response.tool_calls
    if not calls:
        return {"ok": False, "detail": f"no tool call; text: {(response.content or '')[:200]!r}"}
    call = calls[0]
    if call.name != SQL_TOOL.name:
        return {"ok": False, "detail": f"called {call.name!r} instead of {SQL_TOOL.name!r}"}
    if call.arguments_error or not isinstance(call.arguments.get("sql"), str):
        return {"ok": False, "detail": f"bad arguments: {call.arguments_error or call.arguments}"}
    return {"ok": True, "detail": call.arguments["sql"]}


def check_repeatable(llm: LLMClient, repeats: int = 3, busy: int = 8) -> dict[str, Any]:
    """Ask the same prompt alone, then again while `busy` other requests run, `repeats` times."""
    answers: list[str] = []
    try:
        for attempt in range(repeats):
            answers.append(_ask(llm, REPEAT_PROMPT, f"preflight-alone-{attempt}").content or "")
            with ThreadPoolExecutor(max_workers=busy + 1) as pool:
                noise = [
                    pool.submit(_ask, llm, BUSY_PROMPTS[i % len(BUSY_PROMPTS)], f"busy-{i}")
                    for i in range(busy)
                ]
                mine = pool.submit(_ask, llm, REPEAT_PROMPT, f"preflight-busy-{attempt}")
                answers.append(mine.result().content or "")
                for future in noise:
                    future.result()
    except LLMError as error:
        return {"ok": False, "distinct": 0, "detail": f"request failed: {error}"}
    distinct = len(set(answers))
    detail = "identical" if distinct == 1 else f"{distinct} different answers of {len(answers)}"
    return {"ok": distinct == 1, "distinct": distinct, "detail": detail}


def measure_throughput(llm: LLMClient, requests: int = 32, concurrency: int = 16) -> dict[str, Any]:
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        responses = list(
            pool.map(
                lambda i: _ask(llm, BUSY_PROMPTS[i % len(BUSY_PROMPTS)], f"throughput-{i}"),
                range(requests),
            )
        )
    seconds = max(time.monotonic() - started, 1e-9)
    output_tokens = sum(response.usage.output_tokens for response in responses)
    return {
        "requests": requests,
        "concurrency": concurrency,
        "seconds": round(seconds, 2),
        "output_tokens": output_tokens,
        "output_tokens_per_second": round(output_tokens / seconds, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a local model server before an eval run.")
    parser.add_argument("--routes", required=True, help="for example vllm:Qwen/Qwen3-14B")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--busy", type=int, default=8)
    parser.add_argument("--requests", type=int, default=32)
    parser.add_argument("--concurrency", type=int, default=16)
    args = parser.parse_args()

    ledger = CostLedger()
    llm = LLMClient(build_routes(args.routes, os.environ), ledger, max_retries=1)
    report = {"tool_call": check_tool_call(llm)}
    report["repeatable"] = check_repeatable(llm, args.repeats, args.busy)
    report["throughput"] = measure_throughput(llm, args.requests, args.concurrency)
    report["cost_usd"] = ledger.totals().cost_usd
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["tool_call"]["ok"] and report["repeatable"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
