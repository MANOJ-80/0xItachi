import asyncio
import os
import logging
from src.core.config import GROQ_KEY
from langchain_openai import ChatOpenAI
from browser_use import Agent, Browser
from pydantic import Field

class CustomChatOpenAI(ChatOpenAI):
    provider: str = Field(default="openai", exclude=True)

# Set up logging for observability (PRD Section 48/83)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("0xItachi")

async def main():
    logger.info("Initializing 0xItachi...")

    # 1. Initialize Brain Providers with Fallback Router
    from src.core.config import OPEN_ROUTER_KEY, NVIDIA_KEY, GROQ_KEY
    from tests.test_real_life_tasks import FaultTolerantChatRouter, CustomChatOpenAI
    
    llm_nvidia = CustomChatOpenAI(
        model="meta/llama-3.2-90b-vision-instruct",
        api_key=NVIDIA_KEY,
        base_url="https://integrate.api.nvidia.com/v1",
        temperature=0.0
    )
    
    llm_openrouter = CustomChatOpenAI(
        model="stealth/ox-alpha",
        api_key=OPEN_ROUTER_KEY,
        base_url="https://openrouter.ai/api/v1",
        temperature=0.0
    )
    
    llm_groq = CustomChatOpenAI(
        model="llama-3.3-70b-versatile",
        api_key=GROQ_KEY,
        base_url="https://api.groq.com/openai/v1",
        temperature=0.0
    )
    
    llm = FaultTolerantChatRouter(models=[llm_openrouter, llm_nvidia, llm_groq])
    logger.info("Brain Provider configured: OpenRouter ox-alpha -> NVIDIA 90B -> Groq 70B (Fault Tolerant)")

    # 2. Initialize Browser execution layer (Browser Use)
    from browser_use.browser.browser import Browser, BrowserConfig
    browser = Browser(config=BrowserConfig(headless=False, disable_security=True))
    
    # Task definition
    task_goal = "Open GitHub and search for Browser Use. Find the repository and summarize what it does."
    logger.info(f"Task Goal: {task_goal}")

    # 3. Create Agent with Workflow Memory (0xItachi orchestration wrapping Browser Use)
    from browser_use.controller.service import Controller
    from src.core.memory import WorkflowManager
    
    workflow_manager = WorkflowManager()
    existing_wf = workflow_manager.find_matching_workflow(task_goal)
    message_context = workflow_manager.format_workflow_hint(existing_wf) if existing_wf else None
    if message_context:
        logger.info("🧠 Workflow Memory Context loaded for task execution.")
    
    controller = Controller(exclude_actions=['go_back', 'save_pdf', 'get_sheet_contents', 'clear_selected_range'])
    
    agent = Agent(
        task=task_goal,
        llm=llm,
        browser=browser,
        controller=controller,
        message_context=message_context
    )

    # 4. Execute Task & Store Learned Workflow
    try:
        logger.info("Starting agent execution...")
        result = await agent.run()
        logger.info("Task completed successfully.")
        logger.info(f"Result:\n{result}")
        workflow_manager.save_workflow_from_history(task_goal, result)
    except Exception as e:
        logger.error(f"Task failed: {e}")
    finally:
        logger.info("Browser session completed.")

if __name__ == "__main__":
    asyncio.run(main())
