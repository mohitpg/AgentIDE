"""
Vision agent — skills: analyze-image, ocr, diagram-to-code.

An independent A2A service that analyzes images with a multimodal model. Images
arrive embedded in the turn text (data URI, image URL, or local path); heavy jobs
run off the request path on the Celery ``vision`` queue.
"""

from agent_vision.agent import VisionAgent, vision_llm_config

__all__ = ["VisionAgent", "vision_llm_config"]
