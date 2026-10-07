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
            - Describe tests I could run to test the agent comprehensively


Chronological Account:

    I started by reading the Assignment requirements. Once I understood the requirements, I thought of an Agent that would satisfy the requirements. I mapped out the overall description, the MCPs I wanted to use, and drew out a pseudo-LangGraph. I then asked ChatGPT:

    "Review [Assignment_1_Description.pdf]. [plan.md] describes my desired agent and the MCPs I want to use. Let me know if it fits the assignment description."

    It identified that I need to explicitly use conversational memory, which I added into the compile step. I also added something in plan.md to distinguish the difference between conversational memory and the saved files in filestore.

    Once that was done, I asked the agent to guide me through setting up the LangGraph:

    "Guide me on coding the LangGraph for this. Use the local gemma4:e4b model (with ollama) as the LLM backend for now, but later when we're ready to push to cloud we will test it using NVIDIA NIM Nemotron."

    It described to me the library requirements, which I put into a requirements.txt. It then helped me set up a .env file that allows me to configure my LLM backend and MCP credentials.

    It then guided me through setting up my LangGraph starting with the LLM backend, then the MCP configuration and discovery, and finally the graph construction. It also told me how to set up FastAPI to expose a "chat" endpoint.

    Once this was done, I went over what type of tests I could do to test my agent. 

    "Give me comprehensive tests for the agent to verify functionality. Ensure that the tests include specific tests for MCP integration correctness, agent reasoning (tool selection) and conversational memory."

    It gave me a long list of tests which I executed. See verification narrative for more details.

    I did run into some failed tests at this point. Namely, I had issues with Tavily, which requested me to use a production key since I was making too many subsequent requests. I asked my agent if this looked right and they agreed, so I went ahead and created one.


Verification Narrative: How you tested your code beyond running it once. Did you
write tests? Did you inspect tool execution? Did you compare against reference
documentation? Did you probe error handling by making an MCP server unavailable?
Vibe-coding without verification does not demonstrate mastery.

    My tests were structured to ensure all of the desired functionality for this agent. I used these as the baseline for correctness for this agent and it gave me insight on how well the agent was functioning. I inspected tool execution to find issues when the tests weren't producing the result I wanted, and it helped me decide what to change in terms of implementation.

What I learned: 2–3 sentences on what the AI tools couldn’t do that you had to
figure out yourself. This is the pedagogical anchor — the process log demonstrates
the human-in-the-loop learning we teach in this course.