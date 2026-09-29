# This is where we do API exposure
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from agent import AgentRuntime, build_agent_runtime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str = Field(min_length=1, max_length=255)


class ChatResponse(BaseModel):
    response: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runtime = await build_agent_runtime()
    logger.info(
        "Available tools: %s",
        app.state.runtime.tool_names,
    )
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    runtime: AgentRuntime = app.state.runtime
    return {
        "status": "ok",
        "tools": runtime.tool_names,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    runtime: AgentRuntime = app.state.runtime

    try:
        result = await runtime.graph.ainvoke(
            {
                "messages": [
                    HumanMessage(content=request.query)
                ]
            },
            config={
                "configurable": {
                    "thread_id": request.session_id,
                },
                "recursion_limit": 12,
            },
        )
    except Exception:
        logger.exception("Unhandled agent failure")
        return ChatResponse(
            response=(
                "I couldn't complete that request because an "
                "agent or tool connection failed. Please try again."
            )
        )

    content = result["messages"][-1].content

    if not isinstance(content, str):
        content = str(content)

    return ChatResponse(response=content)