Kendrick Ng
Professor Papa
CSCI599
13th October 2026

Process Log

AI Tools used:

    ChatGPT: GPT-5.6-Sol; High reasoning:
        Purpose:
            - Process my plan.md and verify that the plan fits the assignment, makes sense, and will hit all the points on the rubric
            - Aid in coding the LangGraph and setting up FastAPI
            - 


Chronological Account:

    I started by reading the Assignment requirements. Once I understood the requirements, I thought of an Agent that would satisfy the requirements. I mapped out the overall description, the MCPs I wanted to use, and drew out a pseudo-LangGraph. I then asked ChatGPT:

    "Review [Assignment_1_Description.pdf]. [plan.md] describes my desired agent and the MCPs I want to use. Let me know if it fits the assignment description."

    It identified that I need to explicitly use conversational memory, which I added into the compile step. I also added something in plan.md to distinguish the difference between conversational memory and the saved files in filestore.

    Once that was done, I asked the agent to guide me through setting up the LangGraph:

    "Guide me on coding the LangGraph for this. Use the local gemma4:e4b model (with ollama) as the LLM backend for now, but later when we're ready to push to cloud we will test it using NVIDIA NIM Nemotron."

    It described to me the library requirements, which I put into a requirements.txt. It then helped me set up a .env file that allows me to configure my LLM backend and MCP credentials.

    It then guided me through setting up my LangGraph starting with the LLM backend, then the MCP configuration and discovery, and finally the graph construction. It also told me how to set up FastAPI to expose a "chat" endpoint.


Verification Narrative: How you tested your code beyond running it once. Did you
write tests? Did you inspect tool execution? Did you compare against reference
documentation? Did you probe error handling by making an MCP server unavailable?
Vibe-coding without verification does not demonstrate mastery.

What I learned: 2–3 sentences on what the AI tools couldn’t do that you had to
figure out yourself. This is the pedagogical anchor — the process log demonstrates
the human-in-the-loop learning we teach in this course.