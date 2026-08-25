import asyncio
import os
import sys
import logging
from dotenv import load_dotenv
load_dotenv()

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser_use import Agent, Browser
from browser_use.browser.browser import BrowserConfig
from browser_use.controller.service import Controller

from src.core.agent_harness import build_robust_llm
from src.core.memory import WorkflowManager

# Configure logger
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("0xItachi-Tests")

async def run_task(task_description: str):
    logger.info(f"\n🚀 Running Task: {task_description}")
    llm = build_robust_llm()
    
    workflow_manager = WorkflowManager()
    existing_wf = workflow_manager.find_matching_workflow(task_description)
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
    
    browser = Browser(config=BrowserConfig(headless=False, disable_security=True))
    controller = Controller(exclude_actions=['go_back', 'save_pdf', 'get_sheet_contents', 'clear_selected_range'])
    
    agent = Agent(
        task=task_description,
        llm=llm,
        browser=browser,
        controller=controller,
        message_context=message_context
    )

    try:
        result = await agent.run()
        logger.info(f"✅ Task Completed Successfully!")
        logger.info(f"Summary Result: {result}")
        workflow_manager.save_workflow_from_history(task_description, result)
    except Exception as e:
        logger.error(f"❌ Task Execution Error: {e}")
    finally:
        await browser.close()

if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else (
        "Go to https://www.saucedemo.com, login with username 'standard_user' and password 'secret_sauce', "
        "sort products by 'Price (low to high)', add the cheapest product and the most expensive product to cart, "
        "navigate to cart, click checkout, fill First Name with 'Autonomous', Last Name with 'Agent', "
        "Zip Code with '90210', click Continue, and extract the Total Price from the summary page"
    )
    asyncio.run(run_task(task))
