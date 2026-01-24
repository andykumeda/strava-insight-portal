import asyncio
import os
import sys
from pathlib import Path

# Add the project root to sys.path so we can import backend modules
sys.path.append(str(Path(__file__).parent.parent))

from backend.llm_provider import get_llm_provider

async def test_llm():
    print("Testing LLM connectivity (OpenRouter)...")
    try:
        provider = get_llm_provider()
        prompt = "Hello! This is a test query to verify connectivity. Please respond with a short sentence confirming you can hear me."
        system_instruction = "You are a helpful assistant."
        
        print(f"Provider: {provider.provider}")
        print(f"Model: {provider.model}")
        
        response = await provider.generate(
            prompt=prompt,
            system_instruction=system_instruction,
            temperature=0.3
        )
        
        print("\n--- LLM RESPONSE ---")
        print(response)
        print("--------------------")
        print("\n✅ LLM test successful!")
        
    except Exception as e:
        print(f"\n❌ LLM test failed: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_llm())
