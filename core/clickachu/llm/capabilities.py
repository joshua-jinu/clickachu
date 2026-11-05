"""Explicit capabilities; never infer reasoning visibility from a model's prose."""

from dataclasses import dataclass

from clickachu.api.protocol import Settings


@dataclass(frozen=True)
class ModelCapabilities:
    images: bool = False
    reasoning_effort: str = "off"


def capabilities(settings: Settings, model: str) -> ModelCapabilities:
    # Unknown/local models stay text-only unless the user explicitly opts in.
    known_vision = settings.provider == "gemini" and model.startswith("gemini-")
    images = settings.vision_mode == "on" or (settings.vision_mode == "auto" and known_vision)
    return ModelCapabilities(images=images, reasoning_effort=settings.reasoning_effort)
