"""Concrete scene library — wraps the 4-level tag taxonomy
(9 L1 / 39 L2 / 101 L3 / 2231 L4) for grounding LLM prompts in real scenes.
"""
from .library import SceneLibrary, Tag, default_library

__all__ = ["SceneLibrary", "Tag", "default_library"]
