"""
fiesta.markov.cli — Flask CLI for Markov-L2 operations.

Registered from main.py:

    from fiesta.markov.cli import register_cli as register_markov_cli
    register_markov_cli(app)

Exposes::

    flask markov backfill            # dry-run: counts only, no writes
    flask markov backfill --commit   # actually insert rows

Idempotent — users with existing history are skipped.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)


def register_cli(app) -> None:
    """Register the ``flask markov`` AppGroup on `app`. Idempotent —
    re-registering does nothing if the group is already present."""
    import click
    from flask.cli import AppGroup

    if any(cmd.name == "markov" for cmd in app.cli.commands.values()):
        return

    markov_cli = AppGroup(
        "markov", help="Markov Layer 2 (user_state_history) operations."
    )

    @markov_cli.command("backfill")
    @click.option(
        "--commit",
        is_flag=True,
        default=False,
        help="Actually insert rows. Omit for a dry-run (counts only).",
    )
    def backfill_cmd(commit: bool) -> None:
        """Seed user_state_history for the pre-launch user cohort.

        Dry-run by default — pass --commit to write. Idempotent: users
        with at least one existing UserStateHistory row are skipped.
        """
        from fiesta.markov.backfill import backfill_all_users

        summary = backfill_all_users(commit=commit)
        mode = "COMMIT" if commit else "DRY-RUN"
        click.echo(f"markov.backfill {mode}:")
        click.echo(f"  seen                 : {summary['seen']}")
        click.echo(f"  already_have_history : {summary['already_have_history']}")
        click.echo(f"  would_seed           : {summary['would_seed']}")
        click.echo(f"  seeded               : {summary['seeded']}")
        click.echo(f"  skipped_no_state     : {summary['skipped_no_state']}")
        click.echo(f"  errors               : {len(summary['errors'])}")
        if summary["errors"]:
            click.echo("  ---- error sample (first 10) ----")
            for uid, err in summary["errors"][:10]:
                click.echo(f"    user_id={uid}: {err}")
        if summary["errors"] and not summary["would_seed"]:
            sys.exit(2)

    app.cli.add_command(markov_cli)


__all__ = ["register_cli"]
