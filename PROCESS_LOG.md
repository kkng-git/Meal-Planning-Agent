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
        
    Gemma4:e4b:
        Purpose:
            - My local testing LLM

    OpenAI GPT-5-Mini:
        Purpose:
            - LLM used in deployment instance


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

    I did run into some failed tests at this point. Namely, I had some weird behavior where my testing model, Gemma, would fail during certain tool calls and even generate their own searches with Tavily. As a result, I had to use my agent to debug that as well. It was an issue with how large my inputs were, which was too much to fit in the context window for my testing model Gemma4:e4b. 

    The agent suggested that I create a model alias with a larger context window, but I told it to approach it differently; Check out the "what I learned section" to read more.

    Once this issue was fixed the tests passed and I was ready to set up for Cloud deployment. I actually ended up choosing to use OpenAI's GPT5-mini with minimal reasoning after hearing some of the concerns with random latency spikes from my classmate.

    My agent helped me Dockerize my agent and deploy it onto Google Cloud Run, where I tested it once more with a modified test suite.


Verification Narrative:

    My tests were structured to ensure all of the desired functionality for this agent. I used these as the baseline for correctness for this agent and it gave me insight on how well the agent was functioning. I inspected tool execution to find issues when the tests weren't producing the result I wanted, and it helped me decide what to change in terms of implementation. With the help of my agent, I completed 12 local tests, 11 live tests, Cloud Logging inspection, simulated MCP outage, malformed response handling, tool errors, memory, and session isolation. I believe this is comprehensive, as it helped us improve implementation to handle the desired use cases more effectively.

What I learned: 

    AI sometimes provides good solutions to my problems. However, in this case, it opted to use a larger model instead of using the inputs optimally. I realized that we could eliminate most of the tools and also compact the text so that the execution could fit inside the smaller context window. I did this instead of the provided solution, and it worked well!