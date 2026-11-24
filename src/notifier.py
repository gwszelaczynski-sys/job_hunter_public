"""Send scan and application notifications via Telegram or email."""


def notify(message: str) -> None:
    """Send a notification through the configured channel."""
    raise NotImplementedError("Configure a notification channel.")
