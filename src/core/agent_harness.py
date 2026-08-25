import asyncio
import logging
import re
import copy
from typing import Any, List, Optional
from collections import deque
from pydantic import Field

from langchain_openai import ChatOpenAI
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.callbacks.manager import CallbackManagerForLLMRun, AsyncCallbackManagerForLLMRun
from langchain_core.output_parsers import PydanticOutputParser

# Configure logger
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("0xItachi-Harness")

class ActionHistoryTracker:
    """
    Harness-level loop prevention state tracker.
    Maintains a ring buffer of recent actions, tracks repeated DOM element interactions,
    and injects dynamic warning snippets into the prompt context.
    """
    def __init__(self, maxlen: int = 5):
        self.history = deque(maxlen=maxlen)
        self.last_url = None

    def record_action(self, action_name: str, params: dict, current_url: str = None) -> dict:
        sig = f"{action_name}:{params.get('index', '')}:{params.get('url', '')}"
        repeat_count = 1
        for item in reversed(self.history):
            if item['sig'] == sig:
                repeat_count += 1
            else:
                break

        url_changed = True
        if current_url and self.last_url and current_url == self.last_url:
            url_changed = False
        if current_url:
            self.last_url = current_url

        record = {
            'action_name': action_name,
            'params': params,
            'sig': sig,
            'repeat_count': repeat_count,
            'url_changed': url_changed
        }
        self.history.append(record)
        return record

    def get_history_prompt_snippet(self) -> str:
        if not self.history:
            return ""

        lines = ["\n[HARNESS ACTION HISTORY & STATE CHECK]"]
        for i, item in enumerate(self.history, 1):
            status = "Progress Made (URL Changed)" if item['url_changed'] else "No Page Outcome Change"
            lines.append(f"  Step -{len(self.history)-i+1}: Action '{item['action_name']}' on target {item['params']} -> Status: {status}")

        last_item = self.history[-1]
        if last_item['repeat_count'] >= 2 and not last_item['url_changed']:
            lines.append(
                f"🚨 WARNING: You have issued the exact same action '{last_item['action_name']}' with params {last_item['params']} "
                f"{last_item['repeat_count']} times consecutively with NO page change! DO NOT repeat this action index again. Choose a different element or strategy."
            )
        return "\n".join(lines)

# Global tracker instance
action_tracker = ActionHistoryTracker()

class CustomChatOpenAI(ChatOpenAI):
    provider: str = Field(default="openai", exclude=True)

class FaultTolerantRunnable:
    """
    Wrapper around a chain of structured output runnables to handle provider failover.
    """
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
        await asyncio.sleep(2.5)
        for r in self.runnables:
            try:
                res = await asyncio.wait_for(r.ainvoke(input_data, config=config, **kwargs), timeout=90.0)
                return self._sanitize(res)
            except asyncio.TimeoutError:
                logger.warning("⚠️ Provider timed out after 90s. Falling back to next provider...")
                last_err = TimeoutError("Provider timed out after 90s")
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
                return await asyncio.wait_for(model._agenerate(messages, stop, run_manager, **kwargs), timeout=90.0)
            except asyncio.TimeoutError:
                logger.warning("⚠️ Model timed out after 90s. Falling back to next...")
                last_exception = TimeoutError("Model timed out after 90s")
            except Exception as e:
                last_exception = e
                logger.warning(f"⚠️ Model failed: {e}. Falling back to next...")
        raise last_exception
        
    def with_structured_output(self, schema, *args, **kwargs):
        kwargs["strict"] = False
        runnables = [model.with_structured_output(schema, *args, **kwargs) for model in self.models]
        return FaultTolerantRunnable(runnables)

class GroqStructuredChatOpenAI(CustomChatOpenAI):
    """
    OpenAI-compatible wrapper with Hermes tool-calling parser support & loop interception.
    """
    def with_structured_output(self, schema, include_raw=False, **kwargs):
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
            if hasattr(parsed, 'action') and isinstance(parsed.action, list) and len(parsed.action) > 0:
                act = parsed.action[0]
                # Check for DoneAction or done field
                if hasattr(act, 'done') and getattr(act, 'done') is not None:
                    return parsed
                if type(act).__name__ == 'DoneAction':
                    return parsed

                act_params = {}
                act_name = type(act).__name__
                if hasattr(act, 'model_dump'):
                    dump = act.model_dump()
                    # Find non-None action key in ActionModel wrapper
                    for k, v in dump.items():
                        if v is not None:
                            act_name = k
                            act_params = v if isinstance(v, dict) else {'value': str(v)}
                            break
                elif hasattr(act, 'dict'):
                    dump = act.dict()
                    for k, v in dump.items():
                        if v is not None:
                            act_name = k
                            act_params = v if isinstance(v, dict) else {'value': str(v)}
                            break

                if act_name == 'done':
                    return parsed
                
                recorded = action_tracker.record_action(act_name, act_params)
                if recorded['repeat_count'] >= 3:
                    logger.warning(f"🚨 HARNESS LOOP BLOCKER TRIGGERED: Action '{act_name}' with params {act_params} repeated 3+ times! Rejecting...")
                    raise ValueError(f"Loop detected: Action '{act_name}' repeated {recorded['repeat_count']} times without progress.")
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
                return await _acall_and_parse(input_data)

        return CustomRunnable()

def build_robust_llm() -> FaultTolerantChatRouter:
    """
    Constructs the multi-provider fault-tolerant LLM router.
    Order:
      0. Groq (openai/gpt-oss-120b)
      1. OpenRouter (meta-llama/llama-3.3-70b-instruct)
      2. NVIDIA NIM (meta/llama-3.1-70b-instruct)
    """
    from src.core.config import GROQ_KEY, NVIDIA_KEY, OPEN_ROUTER_KEY
    
    models = []
    
    # 0. Primary: Groq GPT-OSS 120B (Ultra-fast ~0.8s inference)
    if GROQ_KEY:
        models.append(GroqStructuredChatOpenAI(
            model="openai/gpt-oss-120b",
            api_key=GROQ_KEY,
            base_url="https://api.groq.com/openai/v1",
            temperature=0.0,
            timeout=25.0,
            max_retries=1
        ))

    # 1. Secondary: OpenRouter Llama 3.3 70B Instruct (High quality 70B)
    if OPEN_ROUTER_KEY:
        models.append(GroqStructuredChatOpenAI(
            model="meta-llama/llama-3.3-70b-instruct",
            api_key=OPEN_ROUTER_KEY,
            base_url="https://openrouter.ai/api/v1",
            temperature=0.0,
            timeout=25.0,
            max_retries=1
        ))

    # 2. Tertiary: NVIDIA NIM Llama 3.1 70B Instruct
    if NVIDIA_KEY:
        models.append(GroqStructuredChatOpenAI(
            model="meta/llama-3.1-70b-instruct",
            api_key=NVIDIA_KEY,
            base_url="https://integrate.api.nvidia.com/v1",
            temperature=0.0,
            timeout=25.0,
            max_retries=1
        ))

    return FaultTolerantChatRouter(models=models)
