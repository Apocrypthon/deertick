"""Discord channel — connects via discord.py gateway (no public IP needed)."""

from __future__ import annotations

import asyncio
import io
import logging
import threading
from typing import Any

from src.channels.base import Channel
from src.channels.message_bus import (
    InboundMessageType,
    MessageBus,
    OutboundMessage,
    ResolvedAttachment,
)

logger = logging.getLogger(__name__)

# Discord message character limit
_DISCORD_MAX_CHARS = 1900


def _chunk_text(text: str) -> list[str]:
    """Split text into Discord-safe chunks."""
    if len(text) <= _DISCORD_MAX_CHARS:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:_DISCORD_MAX_CHARS])
        text = text[_DISCORD_MAX_CHARS:]
    return chunks


class DiscordChannel(Channel):
    """Discord bot channel using discord.py gateway (long-polling equivalent).

    Configuration keys (in config.yaml under channels.discord):
        bot_token:     Discord bot token from the Developer Portal.
        allowed_users: Optional list of Discord user IDs (int). Empty = allow all.
    """

    def __init__(self, bus: MessageBus, config: dict[str, Any]) -> None:
        super().__init__(name="discord", bus=bus, config=config)
        self._client = None
        self._thread: threading.Thread | None = None
        self._discord_loop: asyncio.AbstractEventLoop | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._allowed_users: set[int] = set()
        for uid in config.get("allowed_users", []):
            try:
                self._allowed_users.add(int(uid))
            except (ValueError, TypeError):
                pass

    async def start(self) -> None:
        if self._running:
            return
        try:
            import discord
        except ImportError:
            logger.error("discord.py not installed. Run: uv add discord.py")
            return

        bot_token = self.config.get("bot_token", "")
        if not bot_token:
            logger.error("Discord channel requires bot_token in config")
            return

        self._main_loop = asyncio.get_event_loop()
        self._running = True
        self.bus.subscribe_outbound(self._on_outbound)

        # ── build client ─────────────────────────────────────────────────────
        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_ready():
            logger.info(
                "Discord bot logged in as %s (id=%s)", client.user, client.user.id
            )

        @client.event
        async def on_message(message):
            # ignore self
            if message.author == client.user:
                return
            if not self._check_user(message.author.id):
                return

            text = message.content.strip()
            if not text:
                return

            chat_id = str(message.channel.id)
            user_id = str(message.author.id)
            msg_id = str(message.id)

            # thread continuity: replies chain on the parent message id
            if message.reference and message.reference.message_id:
                topic_id = str(message.reference.message_id)
            else:
                topic_id = msg_id

            # slash-style commands
            msg_type = (
                InboundMessageType.COMMAND
                if text.startswith("/")
                else InboundMessageType.CHAT
            )

            inbound = self._make_inbound(
                chat_id=chat_id,
                user_id=user_id,
                text=text,
                msg_type=msg_type,
                thread_ts=msg_id,
            )
            inbound.topic_id = topic_id

            # acknowledge immediately
            if self._main_loop and self._main_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    message.add_reaction("⏳"), self._discord_loop
                )
                asyncio.run_coroutine_threadsafe(
                    self.bus.publish_inbound(inbound), self._main_loop
                )

        # ── run in daemon thread (mirrors TelegramChannel._run_polling) ──────
        self._thread = threading.Thread(
            target=self._run_gateway, args=(bot_token,), daemon=True
        )
        self._thread.start()
        logger.info("Discord channel started")

    async def stop(self) -> None:
        self._running = False
        self.bus.unsubscribe_outbound(self._on_outbound)
        if self._discord_loop and self._client:
            asyncio.run_coroutine_threadsafe(self._client.close(), self._discord_loop)
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        logger.info("Discord channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        if not self._client or not self._discord_loop:
            return
        channel = self._client.get_channel(int(msg.chat_id))
        if channel is None:
            try:
                channel = await asyncio.wrap_future(
                    asyncio.run_coroutine_threadsafe(
                        self._client.fetch_channel(int(msg.chat_id)),
                        self._discord_loop,
                    )
                )
            except Exception:
                logger.error("Discord: cannot resolve channel %s", msg.chat_id)
                return

        for chunk in _chunk_text(msg.text):
            asyncio.run_coroutine_threadsafe(channel.send(chunk), self._discord_loop)

    async def send_file(
        self, msg: OutboundMessage, attachment: ResolvedAttachment
    ) -> bool:
        if not self._client or not self._discord_loop:
            return False
        # Discord file limit: 8MB on free servers
        if attachment.size > 8 * 1024 * 1024:
            logger.warning(
                "Discord: file too large (%d bytes), skipping %s",
                attachment.size,
                attachment.filename,
            )
            return False
        try:
            import discord

            channel = self._client.get_channel(int(msg.chat_id))
            if channel is None:
                return False

            def _read_file() -> bytes:
                with open(attachment.actual_path, "rb") as f:
                    return f.read()

            data = await asyncio.to_thread(_read_file)
            discord_file = discord.File(io.BytesIO(data), filename=attachment.filename)

            asyncio.run_coroutine_threadsafe(
                channel.send(file=discord_file), self._discord_loop
            )
            return True
        except Exception:
            logger.exception("Discord: failed to send file %s", attachment.filename)
            return False

    # ── internal ─────────────────────────────────────────────────────────────

    def _check_user(self, user_id: int) -> bool:
        if not self._allowed_users:
            return True
        return user_id in self._allowed_users

    def _run_gateway(self, bot_token: str) -> None:
        """Run discord.py event loop in a dedicated thread."""
        self._discord_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._discord_loop)
        try:
            self._discord_loop.run_until_complete(self._client.start(bot_token))
        except Exception:
            if self._running:
                logger.exception("Discord gateway error")
        finally:
            try:
                self._discord_loop.run_until_complete(self._client.close())
            except Exception:
                pass
