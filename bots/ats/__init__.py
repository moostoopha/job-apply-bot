from .greenhouse import GreenhouseATS
from .lever import LeverATS
from .personio import PersonioATS


def detect_ats(url: str):
    """Return the right ATS handler class based on URL, or None if unknown."""
    url = url.lower()
    if "greenhouse.io" in url or "boards.greenhouse.io" in url:
        return GreenhouseATS
    if "jobs.lever.co" in url or "lever.co" in url:
        return LeverATS
    if "personio.de" in url or "personio.com" in url:
        return PersonioATS
    return None
