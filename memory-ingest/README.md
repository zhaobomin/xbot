# memory-ingest

Decoupled CLI for scanning external files, extracting durable memories, and
writing them into mem0.

## Commands

```bash
memory-ingest run --config ./memory-ingest.toml
memory-ingest scan --config ./memory-ingest.toml
memory-ingest extract --config ./memory-ingest.toml --dry-run
memory-ingest doctor --config ./memory-ingest.toml
memory-ingest query "用户有哪些长期偏好？" --config ./memory-ingest.toml --top-k 5 --graph
memory-ingest query "用户有哪些长期偏好？" --config ./memory-ingest.toml --format json
memory-ingest serve --config ./memory-ingest.toml --host 127.0.0.1 --port 8767
```

## Config

Copy `memory-ingest.toml.example` and adjust the directories, model config, and
mem0 API settings.

## Scheduling

- macOS `launchd` example: `deploy/com.memory-ingest.plist.example`
- Linux `cron` example: `deploy/memory-ingest.cron.example`
