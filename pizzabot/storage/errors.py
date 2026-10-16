class StorageError(RuntimeError):
    """The database does not hold what the caller expects."""


class ConfigError(StorageError):
    """A restaurant's configuration is missing or incomplete (e.g. no vat_rate)."""
