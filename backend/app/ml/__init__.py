"""Local machine-learning infrastructure.

The runtime pipeline does not use these components until a feature explicitly
opts in. Keeping the package import side-effect free prevents model downloads
or heavyweight initialization during normal API startup.
"""

from backend.app.ml.contracts import EmbeddingModel, EmbeddingModelMetadata
from backend.app.ml.model_registry import ModelRegistry, get_embedding_model

__all__ = [
    "EmbeddingModel",
    "EmbeddingModelMetadata",
    "ModelRegistry",
    "get_embedding_model",
]
