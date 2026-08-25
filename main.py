import asyncio
import sys
import os
import logging

from browser_use import Agent, Browser
from browser_use.browser.browser import BrowserConfig
from browser_use.controller.service import Controller

from src.core.agent_harness import build_robust_llm
from src.core.memory import WorkflowManager

# Set up logging for observability
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("0xItachi")

async def run_agent(task_goal: str):
    logger.info(f"🚀 Starting 0xItachi Agent...")
    logger.info(f"🎯 Goal: {task_goal}")

    # 1. Build Multi-Provider Fault-Tolerant LLM Harness (Groq 120B -> OpenRouter 70B -> NVIDIA NIM 70B)
    llm = build_robust_llm()

    # 2. Check Persistent Memory for workflow hints
    workflow_manager = WorkflowManager()
    existing_wf = workflow_manager.find_matching_workflow(task_goal)
    wf_hint = workflow_manager.format_workflow_hint(existing_wf) if existing_wf else None
    
    system_hint = (
        "IMPORTANT ACTION RULES: To click an element, use action key 'click_element_by_index' with 'index'. "
        "To type text, use 'input_text' with 'index' and 'text'. To navigate, use 'go_to_url' with 'url'. Never leave action empty."
    )
    if wf_hint:
        logger.info("🧠 Persistent Memory Match Found! Injecting procedural workflow hint...")
        message_context = system_hint + "\n\n" + wf_hint
    else:
        message_context = system_hint

    # 3. Setup Browser & Action Controller
    browser = Browser(config=BrowserConfig(headless=False, disable_security=True))
    controller = Controller(exclude_actions=['go_back', 'save_pdf', 'get_sheet_contents', 'clear_selected_range'])

    # 4. Instantiate Agent
    agent = Agent(
        task=task_goal,
        llm=llm,
        browser=browser,
        controller=controller,
        message_context=message_context
    )

    # 5. Run & Save Memory
    try:
        result = await agent.run()
        logger.info("✅ Task completed successfully.")
        logger.info(f"📋 Result:\n{result}")
        workflow_manager.save_workflow_from_history(task_goal, result)
        return result
    except Exception as e:
        logger.error(f"❌ Task failed: {e}")
        raise e
    finally:
        await browser.close()

if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else "Go to https://www.saucedemo.com and log in as standard_user with password secret_sauce"
    asyncio.run(run_agent(task))
