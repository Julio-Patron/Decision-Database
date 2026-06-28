__all__ = ["create_app"]


def create_app(*args, **kwargs):
    """
    Lazily import the FastAPI application factory.

    The ``ses`` console entry point also hosts non-server subcommands such as
    ``ses curate``. Importing FastAPI at package import time makes those
    subcommands fail in environments that only need the curation CLI loaded, so
    keep server-only dependencies behind the actual factory call.
    """
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)
