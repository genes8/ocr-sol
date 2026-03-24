"""Shared LLM utility functions for all worker modules."""


def strip_llm_fences(content: str) -> str:
    """Strip markdown code fences from LLM response before JSON parsing."""
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]
    return content.strip()
