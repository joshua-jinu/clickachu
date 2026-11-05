"""Use the desktop's current local date, never a model's training cutoff."""

from datetime import date, datetime


def today() -> date:
    return datetime.now().astimezone().date()


def date_context() -> str:
    return (
        f"Date: {today().isoformat()}; training cutoff is not today's date. "
        "Verify news online; don't infer absence."
    )
