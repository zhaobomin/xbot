from __future__ import annotations

from datetime import timedelta
import json
import re

import typer
import uvicorn

from .config import load_config
from .formatter import format_query_pretty
from .review_packet import parse_review_packet, validate_review_decisions
from .server import create_app
from .service import IngestService

app = typer.Typer(help="Scan external knowledge files and import durable memories into mem0.")


def _parse_since(value: str | None) -> timedelta | None:
    if not value:
        return None
    match = re.fullmatch(r"(\d+)([smhd])", value.strip())
    if not match:
        raise typer.BadParameter("since must look like 30m, 6h, 2d")
    amount = int(match.group(1))
    unit = match.group(2)
    return {
        "s": timedelta(seconds=amount),
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }[unit]


@app.command()
def scan(
    config: str = typer.Option(..., "--config"),
    source: list[str] = typer.Option(None, "--source"),
    since: str | None = typer.Option(None, "--since"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    cfg = load_config(config, source_overrides=source or None)
    service = IngestService(cfg)
    try:
        items = service.scan(since=_parse_since(since), limit=limit)
        typer.echo(json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False, indent=2))
    finally:
        service.close()


@app.command("extract")
def extract_cmd(
    config: str = typer.Option(..., "--config"),
    source: list[str] = typer.Option(None, "--source"),
    since: str | None = typer.Option(None, "--since"),
    limit: int | None = typer.Option(None, "--limit"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    cfg = load_config(config, source_overrides=source or None)
    service = IngestService(cfg)
    try:
        candidates, mode = service.extract_only(
            since=_parse_since(since),
            limit=limit,
            force=force,
        )
        payload = {
            "mode": mode,
            "dry_run": dry_run,
            "force": force,
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        service.close()


@app.command()
def draft(
    config: str = typer.Option(..., "--config"),
    source: list[str] = typer.Option(None, "--source"),
    since: str | None = typer.Option(None, "--since"),
    limit: int | None = typer.Option(None, "--limit"),
    force: bool = typer.Option(False, "--force"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    cfg = load_config(config, source_overrides=source or None)
    service = IngestService(cfg)
    try:
        packet, packet_path = service.draft_packet(
            since=_parse_since(since),
            limit=limit,
            force=force,
            output_path=output,
        )
        typer.echo(
            json.dumps(
                {
                    "packet_id": packet.packet_id,
                    "packet_path": str(packet_path),
                    "candidate_count": len(packet.candidates),
                    "mem0_user_id": packet.mem0_user_id,
                    "mem0_app_id": packet.mem0_app_id,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        service.close()


@app.command()
def review(
    packet: str = typer.Option(..., "--packet"),
) -> None:
    parsed_packet, decisions = parse_review_packet(packet)
    counts = validate_review_decisions(decisions)
    typer.echo(
        json.dumps(
            {
                "packet_id": parsed_packet.packet_id,
                "candidate_count": len(parsed_packet.candidates),
                "counts": counts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command()
def apply(
    config: str = typer.Option(..., "--config"),
    packet: str = typer.Option(..., "--packet"),
) -> None:
    cfg = load_config(config)
    service = IngestService(cfg)
    try:
        summary = service.apply_review_packet(packet)
        typer.echo(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2))
    finally:
        service.close()


@app.command()
def run(
    config: str = typer.Option(..., "--config"),
    source: list[str] = typer.Option(None, "--source"),
    since: str | None = typer.Option(None, "--since"),
    limit: int | None = typer.Option(None, "--limit"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(False, "--force"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    cfg = load_config(config, source_overrides=source or None)
    service = IngestService(cfg)
    try:
        summary = service.run(
            since=_parse_since(since),
            limit=limit,
            dry_run=dry_run,
            force=force,
        )
        payload = summary.__dict__
        if verbose:
            payload["config"] = cfg.model_dump(mode="json")
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        service.close()


@app.command()
def doctor(
    config: str = typer.Option(..., "--config"),
    source: list[str] = typer.Option(None, "--source"),
) -> None:
    cfg = load_config(config, source_overrides=source or None)
    service = IngestService(cfg)
    try:
        service.get_client().healthcheck()
        payload = {
            "mem0": "ok",
            "sources": cfg.sources.directories,
            "state_db": cfg.state.sqlite_path,
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        service.close()


@app.command()
def query(
    query: str = typer.Argument(...),
    config: str = typer.Option(..., "--config"),
    top_k: int = typer.Option(5, "--top-k"),
    graph: bool = typer.Option(True, "--graph/--no-graph"),
    output_format: str = typer.Option("pretty", "--format"),
) -> None:
    cfg = load_config(config)
    service = IngestService(cfg)
    try:
        result = service.query(query, top_k=top_k, enable_graph=graph)
        if output_format == "json":
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
            return
        if output_format == "pretty":
            typer.echo(format_query_pretty(result))
            return
        raise typer.BadParameter("format must be 'pretty' or 'json'")
    finally:
        service.close()


@app.command()
def serve(
    config: str = typer.Option(..., "--config"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8767, "--port"),
) -> None:
    cfg = load_config(config)
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
