"""Audio package.

``create_vibevoice_router`` is loaded lazily so that importing a pure
submodule (``audio.provider_profile``) does not pull in fastapi/soundfile or
create an import cycle with ``audio_generation_service``.
"""

__all__ = ["create_vibevoice_router"]


def __getattr__(name):
    if name == "create_vibevoice_router":
        from .vibevoice import create_vibevoice_router

        return create_vibevoice_router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
