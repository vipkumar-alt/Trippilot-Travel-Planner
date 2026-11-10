class ProviderError(Exception):
    """A safe, client-facing error from an external travel-data provider."""


class ConfigurationError(ProviderError):
    """A required provider configuration value is unavailable."""


class ConversationError(ProviderError):
    """A safe error from the in-memory conversational agent."""


class ConversationNotFoundError(ConversationError):
    """The requested conversation is unavailable in this process."""


class RevisionConflictError(ConversationError):
    """The client attempted to update a stale itinerary revision."""

