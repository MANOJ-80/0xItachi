import asyncio
import json
import uuid
import logging
from typing import Dict
from pathlib import Path

from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from browser_use import Agent, Browser
from browser_use.browser.browser import BrowserConfig
from browser_use.controller.service import Controller

from src.core.agent_harness import build_robust_llm
from src.core.memory import WorkflowManager

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("0xItachi-API")

app = FastAPI(title="0xItachi Autonomous Browser Agent API", version="1.0.0")

# In-memory task management
active_tasks: Dict[str, asyncio.Queue] = {}
task_results: Dict[str, dict] = {}
running_async_tasks: Dict[str, asyncio.Task] = {}
active_browsers: Dict[str, Browser] = {}
active_agents: Dict[str, Agent] = {}

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

async def run_agent_task(task_id: str, task_goal: str):
    queue = active_tasks.get(task_id)
    def push_event(event_type: str, data: dict):
        if queue:
            queue.put_nowait({"type": event_type, "data": data})

    push_event("status", {"message": f"🚀 Task initialized: '{task_goal}'", "step": 0})

    browser = None
    agent = None
    try:
        push_event("status", {"message": "🧠 Configuring Multi-Provider LLM Router Cascade (Groq 120B -> OpenRouter 70B -> NVIDIA 70B)", "step": 0})
        llm = build_robust_llm()

        workflow_manager = WorkflowManager()
        existing_wf = workflow_manager.find_matching_workflow(task_goal)
        wf_hint = workflow_manager.format_workflow_hint(existing_wf) if existing_wf else None
        
        system_hint = (
            "IMPORTANT ACTION RULES:\n"
            "1. To click an element, use action key 'click_element_by_index' with 'index'.\n"
            "2. To type text into an input field, use 'input_text' with 'index' and 'text'.\n"
            "3. SEARCH & FORM SUBMIT RULE: If a search bar or input field has no separate submit button, use 'send_keys' with keys 'Enter' right after typing into the field!\n"
            "4. To navigate to a URL, use 'go_to_url' with 'url'. Never leave action empty."
        )
        if wf_hint:
            push_event("status", {"message": "💡 Workflow Memory Match Found! Injecting procedural hint...", "step": 0})
            message_context = system_hint + "\n\n" + wf_hint
        else:
            message_context = system_hint

        browser = Browser(config=BrowserConfig(headless=False, disable_security=True))
        active_browsers[task_id] = browser
        controller = Controller(exclude_actions=['go_back', 'save_pdf', 'get_sheet_contents', 'clear_selected_range'])

        def on_step_callback(state, agent_output, step_number):
            try:
                actions_summary = []
                if hasattr(agent_output, "action") and agent_output.action:
                    for act in agent_output.action:
                        act_dict = act.model_dump() if hasattr(act, "model_dump") else act
                        if isinstance(act_dict, dict):
                            for act_name, act_args in act_dict.items():
                                if act_name not in ["interacted_element", "current_state"] and act_args:
                                    actions_summary.append(f"{act_name}({act_args})")
                
                thought = ""
                if hasattr(agent_output, "current_state") and hasattr(agent_output.current_state, "thought"):
                    thought = agent_output.current_state.thought or ""

                step_msg = f"📍 Step {step_number}: Executing [{', '.join(actions_summary)}]"
                if thought:
                    step_msg += f" \n   ↳ 💭 Agent Thinking: \"{thought}\""

                push_event("status", {"message": step_msg, "step": step_number})
            except Exception as err:
                logger.error(f"Error in step callback: {err}")

        agent = Agent(
            task=task_goal,
            llm=llm,
            browser=browser,
            controller=controller,
            enable_memory=True,
            message_context=message_context,
            register_new_step_callback=on_step_callback
        )
        active_agents[task_id] = agent

        push_event("status", {"message": "🌐 Launching Chrome browser session...", "step": 1})
        result = await agent.run()

        output_text = str(result)
        workflow_manager.save_workflow_from_history(task_goal, result)
        
        task_results[task_id] = {"status": "SUCCESS", "result": output_text}
        push_event("complete", {"status": "SUCCESS", "result": output_text})

    except asyncio.CancelledError:
        logger.info(f"🛑 Task {task_id} was manually cancelled by user.")
        push_event("status", {"message": "🛑 Task execution stopped by user.", "step": 0})
        push_event("error", {"error": "Task stopped by user."})
    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}")
        task_results[task_id] = {"status": "ERROR", "error": str(e)}
        push_event("error", {"error": str(e)})
    finally:
        if agent:
            try:
                agent.stop()
            except Exception:
                pass
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        active_agents.pop(task_id, None)
        active_browsers.pop(task_id, None)
        running_async_tasks.pop(task_id, None)

@app.post("/api/run-task")
async def start_task(request: Request):
    body = await request.json()
    task_goal = body.get("task")
    if not task_goal:
        return JSONResponse({"error": "Task goal is required"}, status_code=400)

    task_id = str(uuid.uuid4())
    active_tasks[task_id] = asyncio.Queue()
    
    async_task = asyncio.create_task(run_agent_task(task_id, task_goal))
    running_async_tasks[task_id] = async_task
    
    return {"task_id": task_id, "message": "Task queued successfully."}

@app.post("/api/stop-task/{task_id}")
async def stop_task(task_id: str):
    async_task = running_async_tasks.get(task_id)
    agent = active_agents.get(task_id)
    browser = active_browsers.get(task_id)
    
    if async_task or agent or browser:
        if agent:
            try:
                agent.stop()
            except Exception as e:
                logger.error(f"Error stopping agent: {e}")
                
        if async_task:
            async_task.cancel()
            
        if browser:
            try:
                await browser.close()
            except Exception as e:
                logger.error(f"Error closing browser: {e}")

        return {"status": "SUCCESS", "message": f"Task {task_id} stopped successfully."}
    
    return JSONResponse({"error": "No active running task found with this ID."}, status_code=404)

@app.get("/api/task-stream/{task_id}")
async def stream_task_events(task_id: str):
    queue = active_tasks.get(task_id)
    if not queue:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    async def event_generator():
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=45.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] in ["complete", "error"]:
                    break
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'ping', 'message': 'keep-alive'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/workflows")
async def get_workflows():
    workflow_manager = WorkflowManager()
    return {"workflows": workflow_manager.workflows}

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text())
    return HTMLResponse(content="<h1>0xItachi API Running</h1>")
