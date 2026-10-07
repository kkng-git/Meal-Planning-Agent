# This is the agent file
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

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
  2. fetch the selected page,
  3. validate the ingredients and instructions,
  4. save the result.
- Never invent tool results.
- If a tool fails, explain the limitation and continue when possible.
- Cite the source URL for web recipes.
"""


@dataclass
class AgentRuntime:
    graph: Any
    mcp_clients: list[MultiServerMCPClient]
    tool_names: list[str]

### Model factory

def build_model():
    # Choose LLM backend
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()

    if provider == "ollama":
        # Ollama
        return ChatOllama(
            model=os.getenv("OLLAMA_MODEL", "gemma4:e4b"),
            base_url=os.getenv(
                "OLLAMA_BASE_URL",
                "http://localhost:11434",
            ),
            temperature=0,
            validate_model_on_init=True,
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

def build_mcp_configs() -> dict[str, dict]:

    # 
    data_dir = Path(
        os.getenv("RECIPE_DATA_DIR", "./data")
    ).resolve()
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

    # Given mcp configs, create client objects from their params
    for server_name, server_config in build_mcp_configs().items():
        client = MultiServerMCPClient(
            {server_name: server_config},
            tool_name_prefix=True,
            handle_tool_errors=True,
        )

        try:
            tools = await client.get_tools()
        except Exception:
            logger.exception(
                "Could not discover tools from %s",
                server_name,
            )
            continue

        clients.append(client)
        all_tools.extend(tools)

        logger.info(
            "Discovered %s tools: %s",
            server_name,
            [tool.name for tool in tools],
        )

    return all_tools, clients

# Graph construction
async def build_agent_runtime() -> AgentRuntime:

    # Discover tools 
    tools, clients = await discover_mcp_tools()

    # Configure backend LLM
    model = build_model()
    # Bind tools
    model_with_tools = (
        model.bind_tools(tools)
        if tools
        else model
    )

    # Helper function
    async def call_model(state: MessagesState):
        response = await model_with_tools.ainvoke(
            [
                SystemMessage(content=SYSTEM_PROMPT),
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
    )
