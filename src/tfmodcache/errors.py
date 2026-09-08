"""Errors that map to a non zero exit code with a readable message."""


class TfmodcacheError(Exception):
    """Base class for every error this tool reports to the user."""


class ConfigError(TfmodcacheError):
    """The Terraform configuration could not be read or understood."""


class SourceError(TfmodcacheError):
    """A module source string is not supported."""


class FetchError(TfmodcacheError):
    """A module could not be downloaded."""


class RegistryError(FetchError):
    """A module registry answered in an unexpected way."""
