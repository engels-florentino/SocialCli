"""Read terminal text independently of the runner's color environment."""
import re


def plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
