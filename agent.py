# This is the agent file
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.prebuilt.tool_node import ToolCallRequest

# Load info for LLM, API keys, MCP URLs
load_dotenv()

logger = logging.getLogger(__name__)

# Context
SYSTEM_PROMPT = """
You are a conversational recipe and meal-planning assistant.

Tool policy:
- Use Tavily search when the user wants current recipes or recipe sources.
- Use Fetch to retrieve and inspect a promising recipe URL.
- Use Filesystem to read or save preferences, recipes, and meal plans.
- Do not write a file unless the user asks you to save something.
- Answer general cooking questions without tools when external information is unnecessary.
- For a request to find and save a recipe, normally:
  1. search for candidates,
  2. choose an individual recipe page, not a collection or list page,
  3. fetch only that selected recipe page,
  4. validate the ingredients and instructions,
  5. save the result with both a path and content.
- Never invent tool results.
- If a tool fails, explain the limitation and continue when possible.
- Cite the source URL for web recipes.
"""

# Bind only the MCP tools this agent needs. All tools are still discovered from
# each server at startup, but exposing every filesystem and Tavily schema to a
# small local model consumes most of its context window before it can respond.
BOUND_TOOL_NAMES = {
    "filesystem_read_text_file",
    "filesystem_write_file",
    "filesystem_list_directory",
    "tavily_tavily_search",
    "fetch_fetch",
}

TAVILY_MAX_RESULTS = 2
TAVILY_SNIPPET_LENGTH = 500
FETCH_MAX_LENGTH = 1800


@dataclass
class AgentRuntime:
    graph: Any
    mcp_clients: list[MultiServerMCPClient]
    tool_names: list[str]
    discovered_tool_names: list[str]
    unavailable_servers: dict[str, str]
    missing_tool_names: list[str]

### Model factory

def build_model():
    # Choose LLM backend
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()

    if provider == "ollama":
        # Use Ollama's OpenAI-compatible endpoint so LangChain preserves
        # tool-call IDs when returning MCP tool results to the model.
        ollama_base_url = os.getenv(
            "OLLAMA_BASE_URL",
            "http://localhost:11434",
        ).rstrip("/")

        return ChatOpenAI(
            model=os.getenv("OLLAMA_MODEL", "gemma4:e4b"),
            base_url=f"{ollama_base_url}/v1",
            api_key="ollama",
            temperature=0,
        )

    if provider == "nim":
        # NVIDIA
        return ChatOpenAI(
            model=os.environ["NIM_MODEL"],
            base_url=os.getenv(
                "NIM_BASE_URL",
                "https://integrate.api.nvidia.com/v1",
            ),
            api_key=os.environ["NVIDIA_API_KEY"],
            temperature=0,
        )

    raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")

def recipe_data_dir() -> Path:
    """Return the only directory exposed to the filesystem MCP server."""
    return Path(os.getenv("RECIPE_DATA_DIR", "./data")).resolve()


def build_mcp_configs() -> dict[str, dict]:

    # 
    data_dir = recipe_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    # Return MCP configuration
    return {
        "filesystem": {
            "transport": "stdio",
            "command": "npx",
            "args": [
                "-y",
                "@modelcontextprotocol/server-filesystem",
                str(data_dir),
            ],
            "env": {
                "npm_config_loglevel": "silent",
                "npm_config_update_notifier": "false",
                "npm_config_fund": "false",
                "npm_config_audit": "false",
            },
        },
        "tavily": {
            "transport": "http",
            "url": "https://mcp.tavily.com/mcp",
            "headers": {
                "Authorization": (
                    f"Bearer {os.environ['TAVILY_API_KEY']}"
                )
            },
        },
        "fetch": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "mcp_server_fetch"],
        },
    }

# Tool discovery
async def discover_mcp_tools():
    all_tools = []
    clients = []
    unavailable_servers = {}

    # Given mcp configs, create client objects from their params
    for server_name, server_config in build_mcp_configs().items():
        client = MultiServerMCPClient(
            {server_name: server_config},
            tool_name_prefix=True,
            handle_tool_errors=True,
        )

        try:
            tools = await client.get_tools()
        except Exception as error:
            logger.exception(
                "Could not discover tools from %s",
                server_name,
            )
            unavailable_servers[server_name] = (
                "transport or tool discovery failed "
                f"({type(error).__name__})"
            )
            continue

        clients.append(client)
        all_tools.extend(tools)

        logger.info(
            "Discovered %s tools: %s",
            server_name,
            [tool.name for tool in tools],
        )

    return all_tools, clients, unavailable_servers


def build_system_prompt(tool_names: list[str]) -> str:
    """Describe only the MCP capabilities available in this runtime."""
    available = set(tool_names)
    if available == BOUND_TOOL_NAMES:
        return SYSTEM_PROMPT

    instructions = [
        "You are a conversational recipe and meal-planning assistant.",
        "",
        "Tool policy:",
        "- Use only the tools listed as available at runtime.",
    ]

    if "tavily_tavily_search" in available:
        instructions.append(
            "- Use Tavily search when the user wants current recipes or "
            "recipe sources."
        )
    if "fetch_fetch" in available:
        instructions.append(
            "- Use Fetch to retrieve and inspect a promising recipe URL."
        )
    if "filesystem_read_text_file" in available:
        instructions.append(
            "- Use Filesystem read to retrieve saved preferences, recipes, "
            "and meal plans."
        )
    if "filesystem_list_directory" in available:
        instructions.append(
            "- Use Filesystem list to inspect available saved files."
        )
    if "filesystem_write_file" in available:
        instructions.append(
            "- Use Filesystem write to save preferences, recipes, and meal "
            "plans when requested."
        )

    instructions.extend(
        [
            "- Do not write a file unless the user asks you to save something.",
            "- Answer general cooking questions without tools when external "
            "information is unnecessary.",
        ]
    )

    if {
        "tavily_tavily_search",
        "fetch_fetch",
    }.issubset(available):
        workflow = [
            "- For a request to find a recipe, normally:",
            "  1. search for candidates,",
            "  2. choose an individual recipe page, not a collection or list page,",
            "  3. fetch only that selected recipe page,",
            "  4. validate the ingredients and instructions.",
        ]
        if "filesystem_write_file" in available:
            workflow.append(
                "  5. if the user requested it, save the result with both a path and content."
            )
        instructions.extend(workflow)

    instructions.extend(
        [
            "- Never invent tool results.",
            "- If a tool fails, explain the limitation and continue when possible.",
            "- Cite the source URL for web recipes.",
        ]
    )

    missing = sorted(BOUND_TOOL_NAMES - available)
    availability = (
        "Runtime tool availability:\n"
        f"- Available: {', '.join(sorted(available))}\n"
        f"- Unavailable: {', '.join(missing) or 'none'}\n"
        "Do not attempt or claim an unavailable capability. If the user asks "
        "for one, explain the limitation and continue with the available "
        "parts of the request."
    )

    return "\n".join([*instructions, "", availability])


def _tool_content_as_text(content: Any) -> str:
    """Flatten MCP text blocks into the smaller string models expect."""
    if isinstance(content, str):
        return content

    parts = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
    return "\n".join(parts) or str(content)


def _compact_tavily_result(content: Any) -> str:
    """Keep only the recipe-candidate fields needed by the agent."""
    text = _tool_content_as_text(content)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text[:1800]

    compact_results = []
    for result in payload.get("results", [])[:TAVILY_MAX_RESULTS]:
        compact_results.append(
            {
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "content": str(result.get("content", ""))[
                    :TAVILY_SNIPPET_LENGTH
                ],
            }
        )

    return json.dumps(
        {
            "query": payload.get("query", ""),
            "results": compact_results,
        }
    )


def _default_meal_plan_path(content: Any) -> str:
    """Choose a predictable in-sandbox filename from saved meal content."""
    text = str(content)
    weekday = re.search(
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        text,
        flags=re.IGNORECASE,
    )
    filename = (
        f"{weekday.group(1).lower()}_meal_plan.md"
        if weekday
        else "meal_plan.md"
    )
    return str(recipe_data_dir() / filename)


async def limit_mcp_tool_output(request: ToolCallRequest, execute):
    """Normalize tool arguments and compact results for the local model."""
    tool_call = request.tool_call
    tool_name = tool_call["name"]
    original_args = tool_call.get("args", {})
    args = dict(original_args)

    if tool_name == "tavily_tavily_search":
        args.update(
            {
                "max_results": TAVILY_MAX_RESULTS,
                "search_depth": "basic",
                "include_raw_content": False,
                "include_images": False,
                "include_image_descriptions": False,
            }
        )
    elif tool_name == "fetch_fetch":
        args.update(
            {
                "max_length": FETCH_MAX_LENGTH,
                "start_index": 0,
                "raw": False,
            }
        )
    elif tool_name == "filesystem_write_file":
        # Small local models sometimes omit the required path after composing
        # the file content. Keep the write inside the MCP server's data root.
        requested_path = args.get("path")
        if requested_path:
            filename = Path(str(requested_path)).name
            args["path"] = str(recipe_data_dir() / filename)
        else:
            args["path"] = _default_meal_plan_path(
                args.get("content", "")
            )

    if args != original_args:
        logged_args = args
        if tool_name == "filesystem_write_file":
            logged_args = {
                "path": args["path"],
                "content_characters": len(str(args.get("content", ""))),
            }
        logger.info(
            "Applying local-model limits to %s: %s",
            tool_name,
            logged_args,
        )

    limited_request = request.override(
        tool_call={**tool_call, "args": args}
    )
    result = await execute(limited_request)

    if not isinstance(result, ToolMessage):
        return result

    if tool_name == "tavily_tavily_search":
        compact_content = _compact_tavily_result(result.content)
    elif tool_name == "fetch_fetch":
        compact_content = _tool_content_as_text(result.content)[
            :FETCH_MAX_LENGTH
        ]
    elif tool_name == "filesystem_write_file":
        compact_content = f"Saved successfully to {args['path']}"
    else:
        return result

    logger.info(
        "Compacted %s result to %d characters",
        tool_name,
        len(compact_content),
    )
    return result.model_copy(update={"content": compact_content})

# Graph construction
async def build_agent_runtime() -> AgentRuntime:

    # Discover tools 
    discovered_tools, clients, unavailable_servers = (
        await discover_mcp_tools()
    )

    discovered_names = {tool.name for tool in discovered_tools}
    missing_tools = sorted(BOUND_TOOL_NAMES - discovered_names)

    tools = [
        tool
        for tool in discovered_tools
        if tool.name in BOUND_TOOL_NAMES
    ]

    if not tools:
        raise RuntimeError(
            "No task-relevant MCP tools were discovered. "
            f"Unavailable servers: {sorted(unavailable_servers)}"
        )

    logger.info(
        "Binding task-relevant tools to the model: %s",
        [tool.name for tool in tools],
    )
    if missing_tools:
        logger.warning(
            "Starting with reduced MCP capabilities; missing tools: %s; "
            "unavailable servers: %s",
            missing_tools,
            sorted(unavailable_servers),
        )

    # Configure backend LLM
    model = build_model()
    # Bind tools
    model_with_tools = (
        model.bind_tools(tools)
        if tools
        else model
    )
    system_prompt = build_system_prompt(
        [tool.name for tool in tools]
    )

    # Helper function
    async def call_model(state: MessagesState):
        response = await model_with_tools.ainvoke(
            [
                SystemMessage(content=system_prompt),
                *state["messages"],
            ]
        )

        logger.info(
            "Model response: content=%r tool_calls=%r invalid_tool_calls=%r",
            response.content,
            response.tool_calls,
            response.invalid_tool_calls,
        )

        return {"messages": [response]}

    # Create graph
    builder = StateGraph(MessagesState)

    builder.add_node("agent", call_model)
    builder.add_node(
        "tools",
        ToolNode(
            tools,
            handle_tool_errors=True,
            awrap_tool_call=limit_mcp_tool_output,
        ),
    )

    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        tools_condition,
    )
    builder.add_edge("tools", "agent")

    graph = builder.compile(
        checkpointer=InMemorySaver(),
    )

    return AgentRuntime(
        graph=graph,
        mcp_clients=clients,
        tool_names=[tool.name for tool in tools],
        discovered_tool_names=[
            tool.name for tool in discovered_tools
        ],
        unavailable_servers=unavailable_servers,
        missing_tool_names=missing_tools,
    )
