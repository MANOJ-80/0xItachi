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

# In-memory task event streams
active_tasks: Dict[str, asyncio.Queue] = {}
task_results: Dict[str, dict] = {}

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

async def run_agent_task(task_id: str, task_goal: str):
    queue = active_tasks.get(task_id)
    def push_event(event_type: str, data: dict):
        if queue:
            queue.put_nowait({"type": event_type, "data": data})

    push_event("status", {"message": f"🚀 Task initialized: '{task_goal}'", "step": 0})

    try:
        # Build LLM Cascade
        push_event("status", {"message": "🧠 Configuring Multi-Provider LLM Router Cascade (Groq 120B -> OpenRouter 70B -> NVIDIA 70B)", "step": 0})
        llm = build_robust_llm()

        # Workflow Memory Context
        workflow_manager = WorkflowManager()
        existing_wf = workflow_manager.find_matching_workflow(task_goal)
        wf_hint = workflow_manager.format_workflow_hint(existing_wf) if existing_wf else None
        
        system_hint = (
            "IMPORTANT ACTION RULES: To click an element, use action key 'click_element_by_index' with 'index'. "
            "To type text, use 'input_text' with 'index' and 'text'. To navigate, use 'go_to_url' with 'url'. Never leave action empty."
        )
        if wf_hint:
            push_event("status", {"message": "💡 Workflow Memory Match Found! Injecting procedural hint...", "step": 0})
            message_context = system_hint + "\n\n" + wf_hint
        else:
            message_context = system_hint

        browser = Browser(config=BrowserConfig(headless=False, disable_security=True))
        controller = Controller(exclude_actions=['go_back', 'save_pdf', 'get_sheet_contents', 'clear_selected_range'])

        agent = Agent(
            task=task_goal,
            llm=llm,
            browser=browser,
            controller=controller,
            message_context=message_context
        )

        push_event("status", {"message": "🌐 Launching Chrome browser session...", "step": 1})
        result = await agent.run()

        output_text = str(result)
        workflow_manager.save_workflow_from_history(task_goal, result)
        
        task_results[task_id] = {"status": "SUCCESS", "result": output_text}
        push_event("complete", {"status": "SUCCESS", "result": output_text})

    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}")
        task_results[task_id] = {"status": "ERROR", "error": str(e)}
        push_event("error", {"error": str(e)})

@app.post("/api/run-task")
async def start_task(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    task_goal = body.get("task")
    if not task_goal:
        return JSONResponse({"error": "Task goal is required"}, status_code=400)

    task_id = str(uuid.uuid4())
    active_tasks[task_id] = asyncio.Queue()
    
    background_tasks.add_task(run_agent_task, task_id, task_goal)
    return {"task_id": task_id, "message": "Task queued successfully."}

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
