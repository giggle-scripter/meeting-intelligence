"""Local machine-learning infrastructure.

The runtime pipeline does not use these components until a feature explicitly
opts in. Keeping the package import side-effect free prevents model downloads
or heavyweight initialization during normal API startup.
"""

from backend.app.ml.contracts import (
    ActionLabel,
    ActionPrediction,
    ActionProbabilities,
    EmbeddingModel,
    EmbeddingModelMetadata,
)
from backend.app.ml.model_registry import (
    ModelRegistry,
    get_action_classifier,
    get_embedding_model,
)

__all__ = [
    "ActionLabel",
    "ActionPrediction",
    "ActionProbabilities",
    "EmbeddingModel",
    "EmbeddingModelMetadata",
    "ModelRegistry",
    "get_action_classifier",
    "get_embedding_model",
]
