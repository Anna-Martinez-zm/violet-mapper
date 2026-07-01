"""Everlight CLI — configuration mapper and template management."""

from pathlib import Path
from typing import Optional
import json
import logging
import sys

import click

from . import __version__
from .mapper import SchemaMapper, ConfigFormat
from .templates import FieldLibrary


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", stream=sys.stderr)


@click.group()
@click.version_option(version=__version__, prog_name="violet")
@click.option("-v", "--verbose", is_flag=True)
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """Everlight — Configuration mapper and template templates."""
    setup_logging(verbose)
    ctx.ensure_object(dict)


@main.command()
@click.argument("root", type=click.Path(exists=True))
@click.option("-o", "--output", type=click.Path(), help="Export snapshots as JSON")
def snapshot(root: str, output: Optional[str]) -> None:
    """Discover and snapshot configuration files in a directory."""
    mapper = SchemaMapper()
    snapshots = mapper.snapshot_directory(root)
    click.echo(f"Snapshotted {len(snapshots)} config files")
    by_fmt: dict[str, int] = {}
    for s in snapshots:
        key = s.format.name.lower()
        by_fmt[key] = by_fmt.get(key, 0) + 1
    click.echo(f"By format: {by_fmt}")
    if output:
        data = [
            {
                "path": s.path, "format": s.format.name.lower(),
                "hash": s.content_hash, "keys": s.keys[:20],
                "size_bytes": s.size_bytes, "line_count": s.line_count,
            }
            for s in snapshots
        ]
        Path(output).write_text(json.dumps(data, indent=2, ensure_ascii=False))
        click.echo(f"Exported to {output}")


@main.command()
@click.argument("root", type=click.Path(exists=True))
@click.option("--top", type=int, default=10, help="Show top N templates")
def templates(root: str, top: int) -> None:
    """Build a template templates from configuration files."""
    mapper = SchemaMapper()
    snapshots = mapper.snapshot_directory(root)
    templates = FieldLibrary()
    new = templates.ingest(snapshots)
    click.echo(f"Total snapshots: {len(snapshots)}")
    click.echo(f"Unique templates: {new}")
    click.echo(f"\nTop {top} templates by frequency:")
    for tpl in templates.most_common(top):
        click.echo(f"  [{tpl.format.name.lower()}] {tpl.name}: "
                   f"{tpl.source_count} sources, {len(tpl.keys)} keys")


@main.command()
def formats() -> None:
    """Show information about supported configuration formats."""
    click.echo("Supported configuration formats:")
    for fmt in ConfigFormat:
        if fmt != ConfigFormat.UNKNOWN:
            click.echo(f"  {fmt.name.lower()}")


if __name__ == "__main__":
    main()
