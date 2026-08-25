import asyncio
import os
from dotenv import load_dotenv
load_dotenv()
from pydantic import Field
from langchain_openai import ChatOpenAI
from browser_use import Agent, Browser
from browser_use.controller.service import Controller

class CustomChatOpenAI(ChatOpenAI):
    provider: str = Field(default="openai", exclude=True)

from typing import Any, List, Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.callbacks.manager import CallbackManagerForLLMRun, AsyncCallbackManagerForLLMRun

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
                print(f"Model failed: {e}. Falling back to next...")
        raise last_exception
        
    async def _agenerate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Optional[AsyncCallbackManagerForLLMRun] = None, **kwargs: Any) -> ChatResult:
        last_exception = None
        for model in self.models:
            try:
                return await model._agenerate(messages, stop, run_manager, **kwargs)
            except Exception as e:
                last_exception = e
                print(f"Model failed: {e}. Falling back to next...")
        raise last_exception
        
    def with_structured_output(self, *args, **kwargs):
        print("Initializing structured output with fallbacks...")
        runnables = [model.with_structured_output(*args, **kwargs) for model in self.models]
        if len(runnables) > 1:
            return runnables[0].with_fallbacks(runnables[1:])
        return runnables[0]

async def test_fallbacks():
    llm_primary = CustomChatOpenAI(
        model="invalid-model-name",
        api_key=os.getenv("OPEN_ROUTER_KEY"),
        base_url="https://openrouter.ai/api/v1",
        max_retries=0
    )
    
    llm_fallback = CustomChatOpenAI(
        model="stealth/ox-alpha",
        api_key=os.getenv("OPEN_ROUTER_KEY"),
        base_url="https://openrouter.ai/api/v1",
        max_retries=0
    )
    
    robust_llm = FaultTolerantChatRouter(models=[llm_primary, llm_fallback])
    
    controller = Controller(exclude_actions=['go_back', 'save_pdf', 'get_sheet_contents', 'clear_selected_range'])
    
    print("Testing Agent with FaultTolerantChatRouter...")
    try:
        agent = Agent(
            task="Find the current price of Ethereum.",
            llm=robust_llm,
            controller=controller
        )
        print("Agent initialized successfully!")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error initializing Agent: {e}")

if __name__ == "__main__":
    asyncio.run(test_fallbacks())
