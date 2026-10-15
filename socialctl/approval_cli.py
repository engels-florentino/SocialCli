"""Explicit approval transport shared by terminal and noninteractive commands."""
import sys

import typer


class ApprovalRequired(ValueError):
    """The caller supplied no explicit noninteractive approval."""


def read_approval(value=None, *, approval_stdin=False, prompt='Enter the exact fingerprint to approve'):
    """Read one explicitly authorized source; an open pipe is never implicit consent."""
    if value is not None and approval_stdin:
        raise ValueError('Choose --approval-fingerprint OR --approval-stdin, not both.')
    if value is not None:
        return value
    if approval_stdin:
        typer.echo(f"{prompt} (--approval-stdin):")
        value = sys.stdin.readline(4097)
        if not value:
            raise typer.Abort()
        if len(value) > 4096 or not value.strip():
            raise ValueError('Exact approval missing or invalid on stdin.')
        return value.strip()
    if not sys.stdin.isatty():
        raise ApprovalRequired('Noninteractive approval required: show the complete preview, then pass --approval-fingerprint DIGEST or explicitly pipe one approval line with --approval-stdin. No input was read.')
    return typer.prompt(prompt)
