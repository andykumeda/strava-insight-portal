"""
LLM Provider Abstraction
Supports multiple LLM providers: OpenRouter, DeepSeek, Gemini
Allows easy switching and cost optimization.
"""
import os
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from google import genai
from openai import AsyncOpenAI

# Load .env explicitly (same pattern as database.py)
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)


class LLMProvider:
    """
    Unified interface for multiple LLM providers.
    Supports OpenRouter, DeepSeek, and Gemini.
    """
    
    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "openrouter").lower()
        self.model = os.getenv("LLM_MODEL", "deepseek/deepseek-chat")
        
        # Initialize clients based on provider
        if self.provider == "openrouter":
            self.api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
            if not self.api_key:
                raise ValueError("OPENROUTER_API_KEY not set")
            
            # Securely log confirmation that the key is loaded
            import hashlib
            import logging
            key_hash = hashlib.sha256(self.api_key.encode()).hexdigest()[:16]
            logging.getLogger("uvicorn").info(f"Loaded OpenRouter Key (sha256-hash: {key_hash})")

            # Store referer for headers
            self.referer = os.getenv("OPENROUTER_REFERER", "http://localhost:8000")
            # Keep AsyncOpenAI client for potential future use, but we'll use httpx directly
            self.client = AsyncOpenAI(
                api_key=self.api_key,
                base_url="https://openrouter.ai/api/v1",
            )
        elif self.provider == "deepseek":
            self.api_key = os.getenv("DEEPSEEK_API_KEY")
            if not self.api_key:
                raise ValueError("DEEPSEEK_API_KEY not set")
            self.base_url = "https://api.deepseek.com/v1"
        elif self.provider == "gemini":
            self.api_key = os.getenv("GEMINI_API_KEY")
            if not self.api_key:
                raise ValueError("GEMINI_API_KEY not set")
            self.client = genai.Client(api_key=self.api_key)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")
    
    async def generate(
        self,
        prompt: str,
        system_instruction: str,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        query_type: Optional[str] = None,
        tools: Optional[list] = None,
        messages: Optional[list] = None
    ) -> dict:
        """
        Generate response using configured provider.
        Returns a DICT containing either 'content' (str) or 'tool_calls' (list).
        
        Args:
            prompt: User prompt/question (used if messages is None)
            system_instruction: System instructions
            temperature: Sampling temperature
            max_tokens: Maximum output tokens
            query_type: Optional query type for model selection
            tools: Optional list of tool definitions
            messages: Optional list of full message history (overrides prompt)
        
        Returns:
            Dict with schema:
            {
                "content": str | None,
                "tool_calls": [...]
            }
        """
        # Select model based on query type if using OpenRouter
        model = self._select_model(query_type)
        
        if self.provider == "openrouter":
            return await self._generate_openrouter(prompt, system_instruction, model, temperature, max_tokens, tools, messages)
        elif self.provider == "deepseek":
            # DeepSeek support for tools is model-dependent, assuming yes for now or fallback
            return await self._generate_deepseek(prompt, system_instruction, model, temperature, max_tokens)
        elif self.provider == "gemini":
            return await self._generate_gemini(prompt, system_instruction, temperature, max_tokens)
            
    def _select_model(self, query_type: Optional[str]) -> str:
        """Select best model based on query type."""
        # For now, strictly use the configured model to avoid overriding user preference
        # or switching to models that require credits (like DeepSeek) when using free ones.
        return self.model

    async def _generate_openrouter(
        self,
        prompt: str,
        system_instruction: str,
        model: str,
        temperature: float,
        max_tokens: int,
        tools: Optional[list] = None,
        messages: Optional[list] = None
    ) -> dict:
        """Generate using OpenRouter API via httpx (more reliable than AsyncOpenAI)."""
        import logging
        logger = logging.getLogger(__name__)
        
        logger.info(f"OpenRouter Request: Model={model}, MaxTokens={max_tokens}, Tools={len(tools) if tools else 0}")
        
        # Construct messages payload
        if messages:
             # Use provided history, prepend system instruction
             msgs = [{"role": "system", "content": system_instruction}] + messages
        else:
             # Default single-turn
             msgs = [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ]
        
        payload = {
            "model": model,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": self.referer,
                        "X-Title": "ActivityCopilot",
                    },
                    json=payload,
                    timeout=60.0
                )
                response.raise_for_status()
                data = response.json()
                
                if "error" in data:
                     logger.error(f"OpenRouter API returned error in JSON: {data}")
                     raise ValueError(f"OpenRouter API Error: {data['error']}")
                
                choice = data["choices"][0]
                message = choice["message"]
                
                result = {
                    "content": message.get("content"),
                    "tool_calls": []
                }
                
                if message.get("tool_calls"):
                    for tc in message["tool_calls"]:
                        result["tool_calls"].append({
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"], # JSON string, needs parsing later
                            "id": tc["id"]
                        })
                        
                return result
                
            except httpx.HTTPStatusError as e:
                logger.error(f"OpenRouter HTTP Error: {e.response.status_code} - {e.response.text}")
                raise ValueError(f"OpenRouter HTTP Error {e.response.status_code}: {e.response.text}")
            except Exception as e:
                logger.error(f"OpenRouter Generic Error: {str(e)}")
                raise

    async def _generate_deepseek(
        self,
        prompt: str,
        system_instruction: str,
        model: str,
        temperature: float,
        max_tokens: int
    ) -> dict:
        """Generate using DeepSeek API directly."""
        # TODO: Add tool calling support for DeepSeek if needed
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model.replace("deepseek/", ""),  # Remove prefix if present
                    "messages": [
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=60.0
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return {"content": content, "tool_calls": []}
    
    async def _generate_gemini(
        self,
        prompt: str,
        system_instruction: str,
        temperature: float,
        max_tokens: int
    ) -> dict:
        """Generate using Gemini API."""
        # Clean model name if it has a prefix (OpenRouter style)
        clean_model = self.model.split("/")[-1] if "/" in self.model else self.model
        if not clean_model.startswith("gemini-"):
            clean_model = "gemini-2.0-flash"

        from google.genai import types
        
        response = await self.client.aio.models.generate_content(
            model=clean_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature,
                max_output_tokens=max_tokens
            )
        )
        return {"content": response.text, "tool_calls": []}


# Global instance
_llm_provider: Optional[LLMProvider] = None

def get_llm_provider() -> LLMProvider:
    """Get or create LLM provider instance."""
    global _llm_provider
    if _llm_provider is None:
        _llm_provider = LLMProvider()
    return _llm_provider
