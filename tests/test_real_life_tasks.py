import asyncio
import os
import logging
from dotenv import load_dotenv
load_dotenv()
from pydantic import Field
from langchain_openai import ChatOpenAI
from browser_use import Agent, Browser
from browser_use.browser.browser import BrowserConfig
from browser_use.controller.service import Controller
from typing import Any, List, Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.callbacks.manager import CallbackManagerForLLMRun, AsyncCallbackManagerForLLMRun
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure logger
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("0xItachi-Tests")

class CustomChatOpenAI(ChatOpenAI):
    provider: str = Field(default="openai", exclude=True)

class FaultTolerantRunnable:
    def __init__(self, runnables: list):
        self.runnables = runnables

    def _sanitize(self, res):
        parsed = res.get('parsed') if isinstance(res, dict) else res
        if hasattr(parsed, 'action') and isinstance(parsed.action, list) and len(parsed.action) > 1:
            non_done = []
            for a in parsed.action:
                is_done = False
                if isinstance(a, dict) and 'done' in a:
                    is_done = True
                elif hasattr(a, 'done') and getattr(a, 'done') is not None:
                    is_done = True
                elif type(a).__name__ == 'DoneAction':
                    is_done = True
                
                if not is_done:
                    non_done.append(a)
            
            if non_done:
                parsed.action = non_done
        return res

    def invoke(self, input_data, config=None, **kwargs):
        last_err = None
        for r in self.runnables:
            try:
                res = r.invoke(input_data, config=config, **kwargs)
                return self._sanitize(res)
            except Exception as e:
                logger.warning(f"⚠️ Provider failed during structured execution: {e}. Falling back to next provider...")
                last_err = e
        raise last_err

    async def ainvoke(self, input_data, config=None, **kwargs):
        last_err = None
        # Pace requests by 2.5 seconds to stay strictly under Groq 8000 TPM limit
        await asyncio.sleep(2.5)
        for r in self.runnables:
            try:
                # 35s timeout to account for free tier provider cold starts
                res = await asyncio.wait_for(r.ainvoke(input_data, config=config, **kwargs), timeout=35.0)
                return self._sanitize(res)
            except asyncio.TimeoutError:
                logger.warning("⚠️ Provider timed out after 35s. Falling back to next provider...")
                last_err = TimeoutError("Provider timed out after 35s")
            except Exception as e:
                logger.warning(f"⚠️ Provider failed during structured execution: {e}. Falling back to next provider...")
                last_err = e
        raise last_err

class FaultTolerantChatRouter(BaseChatModel):
    models: List[BaseChatModel]
    
    @property
    def _llm_type(self) -> str:
        return "fault-tolerant-router"
        
    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Optional[CallbackManagerForLLMRun] = None, **kwargs: Any) -> ChatResult:
        last_exception = None
        for model in self.models:
            try:
                return model._generate(messages, stop, run_manager, **kwargs)
            except Exception as e:
                last_exception = e
                logger.warning(f"⚠️ Model failed: {e}. Falling back to next...")
        raise last_exception
        
    async def _agenerate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Optional[AsyncCallbackManagerForLLMRun] = None, **kwargs: Any) -> ChatResult:
        last_exception = None
        for model in self.models:
            try:
                return await asyncio.wait_for(model._agenerate(messages, stop, run_manager, **kwargs), timeout=35.0)
            except asyncio.TimeoutError:
                logger.warning("⚠️ Model timed out after 35s. Falling back to next...")
                last_exception = TimeoutError("Model timed out after 35s")
            except Exception as e:
                last_exception = e
                logger.warning(f"⚠️ Model failed: {e}. Falling back to next...")
        raise last_exception
        
    def with_structured_output(self, schema, *args, **kwargs):
        kwargs["strict"] = False
        runnables = []
        for model in self.models:
            try:
                runnables.append(model.with_structured_output(schema, *args, **kwargs))
            except Exception as e:
                logger.warning(f"Failed to create structured output for model {model}: {e}")
        return FaultTolerantRunnable(runnables)

class ActionHistoryTracker:
    def __init__(self, max_history=5):
        self.history = []
        self.last_url = None
        self.last_action_signature = None
        self.repeat_count = 0
        self.step_counter = 0

    def record_action(self, action_type: str, params: dict, current_url: str = ""):
        self.step_counter += 1
        sig = f"{action_type}:{params}"
        outcome = "unknown"
        if self.last_url and current_url:
            if current_url != self.last_url:
                outcome = "navigation_occurred"
            else:
                outcome = "no_url_change"

        if sig == self.last_action_signature and outcome != "navigation_occurred":
            self.repeat_count += 1
        else:
            self.repeat_count = 1
            self.last_action_signature = sig

        if current_url:
            self.last_url = current_url

        item = {
            "step": self.step_counter,
            "action": action_type,
            "params": params,
            "url": current_url,
            "outcome": outcome,
            "repeat_count": self.repeat_count
        }
        self.history.append(item)
        if len(self.history) > 5:
            self.history.pop(0)
        return item

    def get_history_prompt_snippet(self) -> str:
        if not self.history:
            return ""
        lines = ["\n--- 📊 ACTION HISTORY & VERIFICATION OUTCOMES ---"]
        for h in self.history:
            lines.append(f"- Step {h['step']}: Action `{h['action']}` with params `{h['params']}` | Outcome: `{h['outcome']}` | Repeats: {h['repeat_count']}")
        
        if self.repeat_count >= 2:
            lines.append(f"\n⚠️ ACTION LOOP WARNING: The action `{self.last_action_signature}` was repeated {self.repeat_count} times without page navigation!")
            lines.append("DO NOT repeat this same element index/action. Choose an alternative interactive element or check if form submission failed.")
        lines.append("----------------------------------------------------\n")
        return "\n".join(lines)

# Global tracker instance
action_tracker = ActionHistoryTracker()

class GroqStructuredChatOpenAI(CustomChatOpenAI):
    def with_structured_output(self, schema, include_raw=False, **kwargs):
        from langchain_core.output_parsers import PydanticOutputParser
        import copy, re
        
        parser = PydanticOutputParser(pydantic_object=schema)
        format_instructions = parser.get_format_instructions()
        
        def _truncate_content(text: str, max_chars: int = 4000) -> str:
            if isinstance(text, str) and len(text) > max_chars:
                return text[:max_chars] + "\n...[DOM Truncated]..."
            return text

        def _prepare_messages(messages):
            history_snippet = action_tracker.get_history_prompt_snippet()
            prompt = f"{history_snippet}\nIMPORTANT: You MUST respond ONLY with a valid JSON object matching this schema:\n{format_instructions}"
            if isinstance(messages, list):
                messages_copy = []
                for m in messages:
                    if hasattr(m, 'content'):
                        m_copy = copy.deepcopy(m)
                        if isinstance(m_copy.content, str):
                            m_copy.content = _truncate_content(m_copy.content)
                        messages_copy.append(m_copy)
                    elif isinstance(m, tuple) and len(m) == 2:
                        messages_copy.append((m[0], _truncate_content(str(m[1]))))
                    else:
                        messages_copy.append(m)
                messages_copy.append(('user', prompt))
                return messages_copy
            else:
                return _truncate_content(str(messages)) + "\n\n" + prompt

        def _process_parsed(parsed):
            # Extract action signature and check for loops
            if hasattr(parsed, 'action') and isinstance(parsed.action, list) and len(parsed.action) > 0:
                act = parsed.action[0]
                act_name = type(act).__name__
                act_params = {}
                if hasattr(act, 'model_dump'):
                    act_params = act.model_dump()
                elif hasattr(act, 'dict'):
                    act_params = act.dict()
                
                recorded = action_tracker.record_action(act_name, act_params)
                if recorded['repeat_count'] >= 3:
                    logger.warning(f"🚨 HARNESS LOOP BLOCKER TRIGGERED: Action {act_name} with params {act_params} repeated 3+ times! Escalating/Rejecting...")
                    raise ValueError(f"Loop detected: Action {act_name} repeated {recorded['repeat_count']} times without progress.")
            return parsed

        def _call_and_parse(messages):
            msgs = _prepare_messages(messages)
            res = self.invoke(msgs)
            content = res.content
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                content = match.group(0)
            parsed = parser.parse(content)
            parsed = _process_parsed(parsed)
            if include_raw:
                return {'raw': res, 'parsed': parsed}
            return parsed
            
        async def _acall_and_parse(messages):
            msgs = _prepare_messages(messages)
            res = await self.ainvoke(msgs)
            content = res.content
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                content = match.group(0)
            parsed = parser.parse(content)
            parsed = _process_parsed(parsed)
            if include_raw:
                return {'raw': res, 'parsed': parsed}
            return parsed
            
        class CustomRunnable:
            def invoke(self, input_data, config=None, **kwargs):
                return _call_and_parse(input_data)
            async def ainvoke(self, input_data, config=None, **kwargs):
                # Pace calls by 2 seconds for API limits
                await asyncio.sleep(2.0)
                return await _acall_and_parse(input_data)

        return CustomRunnable()

def build_robust_llm():
    from src.core.config import GROQ_KEY, NVIDIA_KEY, OPEN_ROUTER_KEY, AIHUBMIX_KEY
    
    models = []
    
    # 0. Primary: Groq GPT-OSS 120B
    if GROQ_KEY:
        models.append(GroqStructuredChatOpenAI(
            model="openai/gpt-oss-120b",
            api_key=GROQ_KEY,
            base_url="https://api.groq.com/openai/v1",
            temperature=0.0,
            timeout=35.0,
            max_retries=1
        ))

    # 1. Secondary: OpenRouter Llama 3.3 70B Instruct
    if OPEN_ROUTER_KEY:
        models.append(GroqStructuredChatOpenAI(
            model="meta-llama/llama-3.3-70b-instruct",
            api_key=OPEN_ROUTER_KEY,
            base_url="https://openrouter.ai/api/v1",
            temperature=0.0,
            timeout=35.0,
            max_retries=1
        ))

    # 2. Tertiary: Self-Hosted Google Colab T4 vLLM (Qwen 2.5 7B AWQ - Zero Rate Limits)
    colab_url = "https://templates-doll-closed-qualify.trycloudflare.com/v1"
    models.append(GroqStructuredChatOpenAI(
        model="Qwen/Qwen2.5-7B-Instruct-AWQ",
        api_key="colab-key",
        base_url=colab_url,
        temperature=0.0,
        timeout=35.0,
        max_retries=1
    ))

    # 3. NVIDIA NIM Llama 3.1 70B Instruct
    if NVIDIA_KEY:
        models.append(GroqStructuredChatOpenAI(
            model="meta/llama-3.1-70b-instruct",
            api_key=NVIDIA_KEY,
            base_url="https://integrate.api.nvidia.com/v1",
            temperature=0.0,
            timeout=35.0,
            max_retries=1
        ))

    return FaultTolerantChatRouter(models=models)

async def run_task(task_description: str):
    logger.info(f"\n🚀 Running Task: {task_description}")
    llm = build_robust_llm()
    
    from src.core.memory import WorkflowManager
    workflow_manager = WorkflowManager()
    existing_wf = workflow_manager.find_matching_workflow(task_description)
    wf_hint = workflow_manager.format_workflow_hint(existing_wf) if existing_wf else None
    system_hint = "IMPORTANT ACTION RULES: To click an element, use action key 'click_element_by_index' with 'index'. To type text, use 'input_text' with 'index' and 'text'. To navigate, use 'go_to_url' with 'url'. Never leave action empty."
    if wf_hint:
        logger.info("🧠 Persistent Memory Match Found! Injecting procedural workflow hint...")
        message_context = system_hint + "\n\n" + wf_hint
    else:
        message_context = system_hint
    
    # Running headless=False so we can see the real-life tasks!
    browser = Browser(config=BrowserConfig(headless=False, disable_security=True))
    controller = Controller(exclude_actions=['go_back', 'save_pdf', 'get_sheet_contents', 'clear_selected_range'])
    
    agent = Agent(
        task=task_description,
        llm=llm,
        browser=browser,
        controller=controller,
        message_context=message_context,
        max_actions_per_step=1,
        use_vision=False,
        max_input_tokens=4000
    )
    
    result = await agent.run()
    logger.info(f"✅ Result: {result}")
    workflow_manager.save_workflow_from_history(task_description, result)
    await browser.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_real_life_tasks.py <task_number>")
        print("1: Find affordable laptops on Amazon")
        print("2: Summarize top news on Hacker News")
        print("3: Find the weather forecast for Tokyo")
        sys.exit(1)
        
    task_num = sys.argv[1]
    
    tasks = {
        "1": "Go to amazon.com, search for a gaming laptop under $1000, and return the names of the top 3 results.",
        "2": "Go to news.ycombinator.com, read the top 3 headlines, and summarize what they are about.",
        "3": "Search Google for 'Tokyo weather forecast next 5 days', read the results, and provide a short summary."
    }
    
    if task_num in tasks:
        asyncio.run(run_task(tasks[task_num]))
    else:
        asyncio.run(run_task(task_num))
