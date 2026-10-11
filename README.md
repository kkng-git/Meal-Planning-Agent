# Meal Planning Agent

A LangGraph meal-planning agent that researches recipes, inspects source pages,
saves meal plans and preferences, and remembers conversations by `session_id`.
It exposes a FastAPI service and integrates three real MCP servers.

## Features

- Semantic tool selection: the model can answer directly, call one tool, or
  chain several tools.
- Three MCP integrations with real tool discovery and invocation:
  Filesystem, Tavily Search, and Fetch.
- LangGraph conversational memory scoped by `session_id`.
- Graceful handling of unavailable MCP servers, tool errors, and malformed
  tool content.
- Local Ollama development and OpenAI-backed Cloud Run deployment.
- `POST /chat` API matching the assignment contract.

## Architecture

### 1. System architecture

```mermaid
flowchart LR
    User[API client] -->|POST /chat| API[FastAPI service]
    API -->|query + thread_id| Graph[LangGraph state graph]
    Graph --> Agent[LLM agent node]
    Agent <-->|reasoning and tool calls| OpenAI[OpenAI gpt-5-mini]
    Graph <-->|conversation state| Memory[InMemorySaver checkpointer]
    Agent -->|tool call| ToolNode[LangGraph ToolNode]
    ToolNode --> Limits[Argument normalization and output limits]
    Limits --> MCP[LangChain MCP client wrapper]
    MCP --> FS[Filesystem MCP server]
    MCP --> Tavily[Tavily Search MCP server]
    MCP --> Fetch[Fetch MCP server]
    FS -->|tool result| ToolNode
    Tavily -->|tool result| ToolNode
    Fetch -->|tool result| ToolNode
    ToolNode -->|result message| Agent
    Agent -->|final response| API
    API --> User
```

### 2. Tool invocation flow

```mermaid
sequenceDiagram
    participant U as User
    participant A as FastAPI
    participant G as LangGraph agent
    participant L as OpenAI LLM
    participant M as MCP client
    participant S as MCP server

    U->>A: POST /chat {query, session_id}
    A->>G: Invoke with session_id as thread_id
    G->>L: Messages plus available tool schemas
    alt No tool needed
        L-->>G: Final answer
    else Tool needed
        loop Until the LLM has enough information
            L-->>G: Select tool and arguments
            G->>M: MCP tools/call
            M->>S: Invoke discovered tool
            S-->>M: Tool result or handled error
            M-->>G: Compact tool result
            G->>L: Continue reasoning with result
        end
        L-->>G: Synthesized final answer
    end
    G-->>A: Final message
    A-->>U: {response}
```

### 3. Deployment topology

```mermaid
flowchart TB
    subgraph Local[Local development]
        Env[.env] --> LocalApp[FastAPI + LangGraph]
        Ollama[Optional Ollama gemma4:e4b] --> LocalApp
        OpenAILocal[OpenAI API] --> LocalApp
        LocalApp --> LocalMCP[Filesystem + Tavily + Fetch MCP]
        Docker[Docker build and local container test]
    end

    Source[Source + Dockerfile] -->|gcloud builds submit| Build[Cloud Build]
    Build --> Registry[Artifact Registry]
    Registry --> Run[Cloud Run service]
    ShellEnv[Local .env loaded by deploy.sh] --> Secrets[Secret Manager]
    Secrets -->|OPENAI_API_KEY and TAVILY_API_KEY| Run
    DeployEnv[Non-secret deployment variables] --> Run
    Run --> OpenAICloud[OpenAI API]
    Run --> CloudMCP[Filesystem + Tavily + Fetch MCP]
    Run --> URL[Public Cloud Run URL]
```

## MCP servers

The application discovers every server independently through
`MultiServerMCPClient.get_tools()`, which performs MCP tool discovery. Tool
calls are executed through the returned MCP tools rather than simulated in the
application.

- [Filesystem MCP](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem)
  reads and writes preferences and meal plans inside one configured directory.
- [Tavily MCP](https://docs.tavily.com/documentation/mcp) finds current recipe
  pages and sources.
- [Fetch MCP](https://github.com/modelcontextprotocol/servers/tree/main/src/fetch)
  retrieves the selected recipe page so the agent can inspect it before saving.
  Fetch is the additional third MCP server because search snippets alone are
  insufficient for validating ingredients and instructions.

The small active tool set keeps prompts focused while all server tools are
still discovered for verification. `/health` reports both discovered and bound
tools, plus any degraded server state.

## API

### Health

```http
GET /health
```

### Chat

```http
POST /chat
Content-Type: application/json

{
  "query": "Find a vegetarian pad thai recipe and save it.",
  "session_id": "demo-session"
}
```

Response:

```json
{
  "response": "..."
}
```

Reuse the same `session_id` for follow-up turns. Memory lasts for the lifetime
of the running instance. It is intentionally not durable across Cloud Run
instance replacement or scale-to-zero.

## Local setup

Requirements:

- Python 3.13
- Node.js and npm for Filesystem MCP
- An OpenAI API key
- A Tavily API key
- Optional: Ollama with `gemma4:e4b`

Create the environment:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and replace the placeholder API keys. `.env` is ignored by Git.
The default configuration uses OpenAI `gpt-5-mini`. To use Ollama locally,
set `LLM_PROVIDER=ollama`.

Run the service:

```bash
source .venv/bin/activate
python main.py
```

It listens on `http://localhost:8080` by default.

```bash
curl -sS http://localhost:8080/health
```

## Testing

The live suite starts an isolated FastAPI process and makes real OpenAI,
Tavily, Fetch, and Filesystem MCP calls:

```bash
source .venv/bin/activate
python live_test.py
```

To test the deployed service and verify selected tools through Cloud Logging:

```bash
python live_test.py \
  --base-url https://meal-planning-agent-245569006163.us-west1.run.app
```

It covers discovery, request validation, no-tool reasoning, multi-tool
reasoning, filesystem operations, tool errors, conversation memory, session
isolation, malformed content, and connection failure degradation. The local
suite has 12 cases, including transport-failure injection. The remote-safe
suite omits that destructive fault injection; its latest run passed all 11
cases against Cloud Run.

## Docker

Build locally:

```bash
docker build -t meal-planning-agent .
```

Run with local environment variables:

```bash
docker run --rm -p 8080:8080 --env-file .env meal-planning-agent
```

The image runs as a non-root user, includes Node.js/npm for the Filesystem MCP
server, and preinstalls Fetch MCP's Readability dependencies at build time.
Never copy `.env` into an image.

## Google Cloud Run deployment

Current deployment:

- Project: `meal-planner-agent-511321`
- Region: `us-west1`
- Service: [`meal-planning-agent`](https://meal-planning-agent-245569006163.us-west1.run.app)
- Health: [`/health`](https://meal-planning-agent-245569006163.us-west1.run.app/health)

Revision `meal-planning-agent-00004-9tx` passed all 11 deployment-safe tests,
including OpenAI responses, three-server MCP discovery, Search/Fetch/Filesystem
chaining, error recovery, conversation memory, and session isolation.

Prerequisites:

- Google Cloud CLI installed
- A Google Cloud project with billing enabled
- Permission to enable APIs, run Cloud Build, create Artifact Registry
  repositories and secrets, and deploy Cloud Run services

Initialize the CLI and choose the correct USC Google account and project:

```bash
gcloud init
```

Set `GCP_PROJECT_ID` in `.env`, then deploy:

```bash
chmod +x deploy.sh
./deploy.sh
```

The script:

1. Enables Cloud Run, Cloud Build, Artifact Registry, and Secret Manager.
2. Creates a regional Artifact Registry repository if needed.
3. Creates a dedicated Cloud Run runtime service account.
4. Stores the OpenAI and Tavily keys in Secret Manager.
5. Builds the Docker image with Cloud Build.
6. Deploys the service publicly in `us-west1` with zero minimum and one maximum
   instance.
7. Prints the stable service URL and health-check command.

After deployment, test the live endpoint:

```bash
SERVICE_URL="$(gcloud run services describe meal-planning-agent \
  --region us-west1 --format='value(status.url)')"

curl -sS "$SERVICE_URL/health"

curl -sS -X POST "$SERVICE_URL/chat" \
  -H 'Content-Type: application/json' \
  -d '{"query":"How do I cook white rice?","session_id":"cloud-smoke-test"}'
```

Keep `--max-instances 1` for this assignment because conversational memory is
stored in-process. Files saved by Filesystem MCP are also ephemeral and may be
lost when Cloud Run replaces the instance.

## Cost disclosure

This project can incur usage-based charges from OpenAI, Tavily, Cloud Build,
Artifact Registry, Secret Manager, and Cloud Run. Cloud Run uses
`--min-instances 0`, so it can scale to zero while idle. The deployment caps the
service at one instance, and `gpt-5-mini` is selected to limit model cost. Check
the relevant provider dashboards during testing and leave the service running
through grading as required by the assignment.

## Project structure

- `agent.py` - model configuration, MCP discovery, tool limits, and LangGraph
- `main.py` - FastAPI endpoints and Cloud Run-compatible server entrypoint
- `live_test.py` - real end-to-end functionality suite
- `Dockerfile` - non-root Cloud Run container
- `deploy.sh` - build, secret setup, and deployment automation
- `PROCESS_LOG.md` - development and verification narrative
