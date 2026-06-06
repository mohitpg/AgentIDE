"""Vision tasks – large image analysis / OCR. Implemented in Phase 5."""

from workers.app import celery_app


@celery_app.task(name="workers.tasks.vision.analyze_image", bind=True)
def analyze_image(self, image_id: str, prompt: str) -> dict:  # type: ignore[no-untyped-def]
    """Analyze a stored image with a multimodal LLM. Phase 5."""
    self.update_state(state="PROGRESS", meta={"progress": 0, "message": "Queued"})
    return {"status": "not_implemented", "image_id": image_id}
