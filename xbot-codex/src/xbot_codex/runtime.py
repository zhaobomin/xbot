from __future__ import annotations

from collections.abc import AsyncIterator

from loguru import logger

from xbot_codex.codex.transport import CodexEvent, CodexTransport
from xbot_codex.commands import parse_command
from xbot_codex.config import ServiceConfig
from xbot_codex.events import InboundMessage, OutboundMessage
from xbot_codex.session.store import SessionStore


class CodexRuntime:
    def __init__(
        self,
        config: ServiceConfig,
        session_store: SessionStore,
        transport: CodexTransport | None = None,
    ):
        self.config = config
        self.session_store = session_store
        self.transport = transport or CodexTransport(
            binary_path=config.codex.binary_path,
            env=self._default_transport_env(config),
        )

    async def handle_message(self, msg: InboundMessage) -> AsyncIterator[OutboundMessage]:
        logger.info("Runtime handling: session_key={} content={!r}", msg.session_key, msg.content[:120])
        command = parse_command(msg.content)
        if command:
            async for outbound in self._handle_command(msg, command.name, command.arg):
                yield outbound
            return

        session = self.session_store.get_or_create(msg.channel, msg.chat_id)
        self.session_store.touch(session.session_key)
        session.process_state = "running"
        delta_parts: list[str] = []
        emitted_final = False
        async for event in self.transport.run_prompt(
            session.session_key,
            msg.content,
            model=session.codex_model or self.config.codex.default_model,
            mode=session.codex_mode or self.config.codex.default_mode,
            profile=session.codex_profile or self.config.codex.profile,
            workdir=session.codex_workdir,
        ):
            logger.info("Runtime event: session_key={} type={} delta_chars={} content_chars={}", session.session_key, event.type, len(event.delta), len(event.content))
            if event.type == "error":
                self.session_store.mark_error(session.session_key, event.content or "Codex failed")
                yield self._map_event(msg, event)
                continue
            if event.type == "message.delta":
                if event.delta:
                    delta_parts.append(event.delta)
                continue
            if event.type == "message.final":
                session.process_state = "idle"
                session.last_error = None
                emitted_final = True
                final_content = event.content or "".join(delta_parts)
                yield OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=final_content,
                    metadata={
                        "event_type": "message.final",
                        "message_id": msg.metadata.get("message_id"),
                        "chat_type": msg.metadata.get("chat_type"),
                    },
                )
        if session.process_state == "running":
            session.process_state = "idle"
        if not emitted_final and delta_parts:
            session.last_error = None
            yield OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="".join(delta_parts),
                metadata={
                    "event_type": "message.final",
                    "message_id": msg.metadata.get("message_id"),
                    "chat_type": msg.metadata.get("chat_type"),
                },
            )

    async def _handle_command(
        self, msg: InboundMessage, name: str, arg: str | None
    ) -> AsyncIterator[OutboundMessage]:
        session = self.session_store.get_or_create(msg.channel, msg.chat_id)

        if name == "help":
            yield self._reply(
                msg,
                "Commands: !help !new !reset !stop !status !mode [value] !model [value]",
            )
            return

        if name in {"new", "reset"}:
            self.session_store.reset(session.session_key)
            yield self._reply(msg, "Started a fresh Codex session.")
            return

        if name == "stop":
            stopped = await self.transport.interrupt(session.session_key)
            session.process_state = "idle"
            yield self._reply(msg, "Stopped current Codex task." if stopped else "No running Codex task.")
            return

        if name == "status":
            yield self._reply(
                msg,
                f"Status: {session.process_state}; model={session.codex_model or self.config.codex.default_model or 'default'}; "
                f"mode={session.codex_mode or self.config.codex.default_mode or 'default'}; "
                f"workdir={session.codex_workdir}; "
                f"last_error={session.last_error or 'none'}",
            )
            return

        if name == "model":
            if arg is None:
                yield self._reply(msg, f"Current model: {session.codex_model or self.config.codex.default_model or 'default'}")
                return
            allowed = self.config.codex.allowed_models
            if allowed and arg not in allowed:
                yield self._reply(msg, f"Model not allowed: {arg}")
                return
            session.codex_model = arg
            yield self._reply(msg, f"Model set to {arg}")
            return

        if name == "mode":
            if arg is None:
                yield self._reply(msg, f"Current mode: {session.codex_mode or self.config.codex.default_mode or 'default'}")
                return
            allowed_modes = self.config.codex.allowed_modes
            if allowed_modes and arg not in allowed_modes:
                yield self._reply(msg, f"Mode not allowed: {arg}")
                return
            session.codex_mode = arg
            yield self._reply(msg, f"Mode set to {arg}")
            return

    def _map_event(self, msg: InboundMessage, event: CodexEvent) -> OutboundMessage:
        if event.type == "message.final":
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=event.content,
                metadata={"event_type": event.type},
            )
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=event.delta or event.content,
            metadata={"event_type": event.type},
        )

    def _reply(self, msg: InboundMessage, content: str) -> OutboundMessage:
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
            metadata={
                "message_id": msg.metadata.get("message_id"),
                "chat_type": msg.metadata.get("chat_type"),
            },
        )

    def status(self) -> dict[str, int | str]:
        return {
            "service": self.config.service_name,
            "running_sessions": self.session_store.running_sessions(),
        }

    @staticmethod
    def _default_transport_env(config: ServiceConfig) -> dict[str, str]:
        codex_home = config.codex.home or f"{config.codex.workdir_root.rstrip('/')}/codex-home"
        env = {
            "HOME": codex_home,
            "CODEX_HOME": codex_home,
        }
        if config.codex.proxy:
            env["HTTP_PROXY"] = config.codex.proxy
            env["HTTPS_PROXY"] = config.codex.proxy
            env["ALL_PROXY"] = config.codex.proxy
        if config.codex.no_proxy:
            env["NO_PROXY"] = config.codex.no_proxy
        return env
