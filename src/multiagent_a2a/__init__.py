"""Public API for the multi-agent dispute resolution project."""

from .config import RunConfig

__all__ = ["RunConfig", "run_pipeline"]
__version__ = "1.0.0"


def __getattr__(name: str):
    """Keep package import side-effect free; import the pipeline only on demand."""
    if name == "run_pipeline":
        from .application.pipeline import run_pipeline

        return run_pipeline
    raise AttributeError(name)

