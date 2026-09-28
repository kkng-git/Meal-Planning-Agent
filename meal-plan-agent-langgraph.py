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

load_dotenv()

logger = logging.getLogger(__name__)


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
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()

    if provider == "ollama":
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