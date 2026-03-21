try:
    from importlib.metadata import version
    __version__ = version("nvdiffrast")
except Exception:
    __version__ = "0.0.0"
