"""Process-local model registry with explicit deterministic fallback."""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from backend.app.ml.contracts import EmbeddingModel
from backend.app.ml.action_classifier import (
    ActionClassifierArtifact,
    LinearActionClassifier,
)
from backend.app.ml.embeddings import (
    HASHING_FALLBACK_MODEL,
    EmbeddingModelLoadError,
    HashingEmbeddingModel,
    SentenceTransformerEmbeddingModel,
)

if TYPE_CHECKING:
    from backend.app.config import Settings


EmbeddingLoader = Callable[[str, str], EmbeddingModel]


def _default_embedding_loader(model_name: str, device: str) -> EmbeddingModel:
    return SentenceTransformerEmbeddingModel(model_name, device=device)


class ModelRegistry:
    """Load each requested model configuration at most once per process."""

    def __init__(self, embedding_loader: EmbeddingLoader | None = None) -> None:
        self._embedding_loader = embedding_loader or _default_embedding_loader
        self._embedding_models: dict[tuple[str, str, int, bool], EmbeddingModel] = {}
        self._action_classifiers: dict[str, LinearActionClassifier] = {}
        self._lock = RLock()

    def get_embedding_model(
        self,
        model_name: str,
        *,
        device: str = "cpu",
        fallback_dimension: int = 384,
        allow_fallback: bool = True,
    ) -> EmbeddingModel:
        requested_model = model_name.strip()
        normalized_device = device.strip()
        if not requested_model:
            raise ValueError("model_name must not be empty")
        if not normalized_device:
            raise ValueError("device must not be empty")
        if fallback_dimension <= 0:
            raise ValueError("fallback_dimension must be greater than zero")
        key = (
            requested_model,
            normalized_device,
            fallback_dimension,
            allow_fallback,
        )
        with self._lock:
            cached = self._embedding_models.get(key)
            if cached is not None:
                return cached
            model = self._load_embedding_model(
                requested_model,
                normalized_device,
                fallback_dimension,
                allow_fallback,
            )
            self._embedding_models[key] = model
            return model

    def _load_embedding_model(
        self,
        model_name: str,
        device: str,
        fallback_dimension: int,
        allow_fallback: bool,
    ) -> EmbeddingModel:
        if model_name == HASHING_FALLBACK_MODEL:
            return HashingEmbeddingModel(fallback_dimension)
        try:
            return self._embedding_loader(model_name, device)
        except Exception as exc:
            if not allow_fallback:
                raise EmbeddingModelLoadError(
                    f"Unable to load embedding model {model_name!r}."
                ) from exc
            error_code = f"{type(exc).__module__}.{type(exc).__name__}"
            return HashingEmbeddingModel(
                fallback_dimension,
                requested_model=model_name,
                is_fallback=True,
                load_error_code=error_code,
            )

    def get_action_classifier(self, model_path: str | Path) -> LinearActionClassifier:
        """Load and cache one portable action classifier artifact."""

        resolved_path = str(Path(model_path).expanduser().resolve())
        with self._lock:
            cached = self._action_classifiers.get(resolved_path)
            if cached is not None:
                return cached
            artifact = ActionClassifierArtifact.from_path(resolved_path)
            manifest = artifact.manifest
            dimension = artifact.linear_model.embedding_dimension
            if manifest.embedding_model == HASHING_FALLBACK_MODEL:
                embedding_model: EmbeddingModel = HashingEmbeddingModel(dimension)
            else:
                embedding_model = self.get_embedding_model(
                    manifest.embedding_model,
                    fallback_dimension=dimension,
                    allow_fallback=False,
                )
            classifier = LinearActionClassifier(artifact, embedding_model)
            self._action_classifiers[resolved_path] = classifier
            return classifier

    def clear(self) -> None:
        """Clear cached models, primarily for controlled tests and reloads."""

        with self._lock:
            self._embedding_models.clear()
            self._action_classifiers.clear()


@lru_cache(maxsize=1)
def get_model_registry() -> ModelRegistry:
    """Return the single default registry for this process."""

    return ModelRegistry()


def get_embedding_model(settings: Settings | None = None) -> EmbeddingModel:
    """Resolve the configured embedding model without wiring it into V1."""

    if settings is None:
        from backend.app.config import get_settings

        settings = get_settings()
    return get_model_registry().get_embedding_model(
        settings.embedding_model_name,
        device=settings.embedding_device,
        fallback_dimension=settings.embedding_fallback_dimension,
        allow_fallback=settings.embedding_fallback_enabled,
    )


def get_action_classifier(
    model_path: str | Path | None = None,
    settings: Settings | None = None,
) -> LinearActionClassifier:
    """Resolve the configured action classifier, failing if no path is set."""

    if settings is None and model_path is None:
        from backend.app.config import get_settings

        settings = get_settings()
    resolved = model_path or (settings.action_classifier_model_path if settings else None)
    if not resolved:
        raise ValueError("ACTION_CLASSIFIER_MODEL_PATH is required")
    return get_model_registry().get_action_classifier(resolved)
