"""MCP server exposing Switchboard as a tool for AI assistants.

Runs as a stdio MCP server (the transport Claude Desktop and Claude Code use).
Talks to a running Switchboard HTTP server via httpx, so you must have the
main server running on `SWITCHBOARD_BASE_URL` (default http://localhost:8000)
before launching this.

Tools exposed:
  - route_chat:        send a prompt; Switchboard picks a provider; returns the answer + routing info.
  - estimate_cost:     preview USD cost across every priced model without sending anything.
  - get_routing_decision: show what Switchboard would route this to (no provider call).

Configure in Claude Desktop's config (~/Library/Application Support/Claude/claude_desktop_config.json):

    {
      "mcpServers": {
        "switchboard": {
          "command": "switchboard-mcp",
          "env": {
            "SWITCHBOARD_BASE_URL": "http://localhost:8000",
            "SWITCHBOARD_API_TOKEN": ""
          }
        }
      }
    }

Install the optional dependency first: `pip install -e ".[mcp]"`.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Switchboard")

BASE_URL = os.getenv("SWITCHBOARD_BASE_URL", "http://localhost:8000").rstrip("/")
API_TOKEN = os.getenv("SWITCHBOARD_API_TOKEN", "")


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"
    return headers


def _trim(s: str | None, limit: int = 4000) -> str | None:
    """Truncate long content so tool responses stay digestible by the calling model."""
    if s is None or len(s) <= limit:
        return s
    return s[:limit] + f"\n…(truncated; original was {len(s)} chars)"


@mcp.tool()
async def route_chat(prompt: str, max_tokens: int = 200) -> dict[str, Any]:
    """Send a chat prompt to Switchboard. The router classifies the task and picks
    a provider/model from your YAML rules, then returns the assistant's reply along
    with which model was chosen and how much it cost (USD).

    Args:
        prompt: The user message to send.
        max_tokens: Maximum completion length (default 200).
    """
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{BASE_URL}/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            },
            headers=_headers(),
        )
        r.raise_for_status()
        data = r.json()

    return {
        "content": _trim(data["choices"][0]["message"]["content"]),
        "provider": data["routing"]["selected_provider"],
        "model": data["routing"]["selected_model"],
        "reason": data["routing"]["reason"],
        "tokens": data["usage"]["total_tokens"],
        "cost_usd": data["cost"]["estimated_usd"],
    }


@mcp.tool()
async def estimate_cost(prompt: str, assumed_max_tokens: int = 256) -> dict[str, Any]:
    """Preview what a prompt would cost across every priced model — no upstream
    call, no tokens spent. Useful when you want to decide whether a sub-task
    deserves an expensive model or can be offloaded to a cheaper one.

    Args:
        prompt: The user message to estimate.
        assumed_max_tokens: Worst-case completion length used for the upper bound.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{BASE_URL}/v1/chat/estimate",
            json={
                "messages": [{"role": "user", "content": prompt}],
                "assumed_max_tokens": assumed_max_tokens,
            },
            headers=_headers(),
        )
        r.raise_for_status()
        data = r.json()

    estimates = [
        {
            "provider": e["provider"],
            "model": e["model"],
            "usd_min": e["estimated_usd_min"],
            "usd_max": e["estimated_usd_max"],
        }
        for e in data["estimates"]
        if e["pricing_known"]
    ]

    cheapest = data.get("cheapest") or {}
    most_expensive = data.get("most_expensive") or {}
    return {
        "input_tokens_estimated": data["input_tokens_estimated"],
        "assumed_max_completion_tokens": data["assumed_max_completion_tokens"],
        "estimates": estimates,
        "cheapest": (
            f"{cheapest.get('provider')}/{cheapest.get('model')}" if cheapest else None
        ),
        "most_expensive": (
            f"{most_expensive.get('provider')}/{most_expensive.get('model')}"
            if most_expensive
            else None
        ),
    }


@mcp.tool()
async def get_routing_decision(prompt: str) -> dict[str, Any]:
    """Show what Switchboard would route this prompt to — and why — without
    calling any provider. Useful for debugging routing rules.

    Args:
        prompt: The user message to classify.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{BASE_URL}/v1/chat/route",
            json={"messages": [{"role": "user", "content": prompt}]},
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()


def main() -> None:
    """Entry point for the `switchboard-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
