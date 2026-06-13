"""TelegramChannel — native Telegram Bot API adapter."""

from __future__ import annotations

import logging
import os
import textwrap
import threading
from typing import Any, Dict, List, Optional

from openjarvis.channels._stubs import (
    BaseChannel,
    ChannelHandler,
    ChannelMessage,
    ChannelStatus,
)
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.registry import ChannelRegistry

logger = logging.getLogger(__name__)


@ChannelRegistry.register("telegram")
class TelegramChannel(BaseChannel):
    """Native Telegram channel adapter using the Bot API.

    Parameters
    ----------
    bot_token:
        Telegram Bot API token.  Falls back to ``TELEGRAM_BOT_TOKEN`` env var.
    allowed_chat_ids:
        Comma-separated list of chat IDs allowed to interact.
    parse_mode:
        Message parse mode (``Markdown``, ``HTML``, etc.).
    bus:
        Optional event bus for publishing channel events.
    """

    channel_id = "telegram"

    def __init__(
        self,
        bot_token: str = "",
        *,
        allowed_chat_ids: str = "",
        parse_mode: str = "Markdown",
        bus: Optional[EventBus] = None,
    ) -> None:
        self._token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._allowed_chat_ids = allowed_chat_ids
        self._parse_mode = parse_mode
        self._bus = bus
        self._handlers: List[ChannelHandler] = []
        self._status = ChannelStatus.DISCONNECTED
        self._listener_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # -- connection lifecycle ---------------------------------------------------

    def connect(self) -> None:
        """Start listening for incoming messages via long polling."""
        if not self._token:
            logger.warning("No Telegram bot token configured")
            self._status = ChannelStatus.ERROR
            return

        self._stop_event.clear()
        self._status = ChannelStatus.CONNECTING

        try:
            from telegram.ext import ApplicationBuilder  # noqa: F401

            self._listener_thread = threading.Thread(
                target=self._poll_loop,
                daemon=True,
            )
            self._listener_thread.start()
            self._status = ChannelStatus.CONNECTED
            logger.info("Telegram channel connected (long polling)")
        except ImportError:
            # python-telegram-bot not installed — send-only mode
            logger.info(
                "python-telegram-bot not installed; send-only mode",
            )
            self._status = ChannelStatus.CONNECTED

    def disconnect(self) -> None:
        """Stop the listener thread."""
        self._stop_event.set()
        if self._listener_thread is not None:
            self._listener_thread.join(timeout=5.0)
            self._listener_thread = None
        self._status = ChannelStatus.DISCONNECTED

    # -- send / receive --------------------------------------------------------

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        """Send a message to a Telegram chat via the Bot API."""
        if not self._token:
            logger.warning("Cannot send: no Telegram bot token")
            return False

        try:
            import httpx

            _TELEGRAM_MAX_LEN = 4096
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            chat_id = conversation_id or channel
            chunks = textwrap.wrap(
                content,
                width=_TELEGRAM_MAX_LEN,
                break_long_words=True,
                replace_whitespace=False,
            )
            for chunk in chunks:
                payload: Dict[str, Any] = {
                    "chat_id": chat_id,
                    "text": chunk,
                }
                if self._parse_mode:
                    payload["parse_mode"] = self._parse_mode

                resp = httpx.post(url, json=payload, timeout=10.0)
                if resp.status_code >= 300:
                    logger.warning(
                        "Telegram API returned status %d: %s",
                        resp.status_code,
                        resp.text,
                    )
                    return False
            self._publish_sent(channel, content, conversation_id)
            return True
        except Exception:
            logger.debug("Telegram send failed", exc_info=True)
            return False

    def status(self) -> ChannelStatus:
        """Return the current connection status."""
        return self._status

    def list_channels(self) -> List[str]:
        """Return available channel identifiers."""
        return ["telegram"]

    def on_message(self, handler: ChannelHandler) -> None:
        """Register a callback for incoming messages."""
        self._handlers.append(handler)

    # -- internal helpers -------------------------------------------------------

    def _poll_loop(self) -> None:
        """Long-poll for updates using python-telegram-bot."""
        try:
            from telegram.ext import ApplicationBuilder, MessageHandler, filters
            import asyncio
            import tempfile

            app = ApplicationBuilder().token(self._token).build()

            async def _handle_msg(update, context):
                msg = update.message
                if msg is None:
                    return

                content = msg.text or ""
                
                # Support Voice messages
                if msg.voice:
                    logger.info("Processing incoming voice message...")
                    try:
                        voice_file = await context.bot.get_file(msg.voice.file_id)
                        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
                            temp_path = f.name
                        
                        await voice_file.download_to_drive(temp_path)
                        
                        with open(temp_path, "rb") as f:
                            audio_bytes = f.read()
                            
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass
                            
                        # Transcribe in a separate thread to avoid blocking the event loop
                        def transcribe_audio(data: bytes) -> str:
                            from google import genai
                            from google.genai import types
                            
                            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
                            if not api_key:
                                return ""
                            client = genai.Client(api_key=api_key)
                            
                            for model_name in ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash"]:
                                try:
                                    resp = client.models.generate_content(
                                        model=model_name,
                                        contents=[
                                            types.Part.from_bytes(
                                                data=data,
                                                mime_type="audio/ogg"
                                            ),
                                            "Ushbu ovozli xabarni to'liq matnga aylantiring (transkripsiya qiling). Hech qanday qo'shimcha izoh yoki tushuntirish yozmang. Faqat eshitilgan gaplarni o'z holicha matnga o'giring."
                                        ]
                                    )
                                    if resp.text:
                                        return resp.text.strip()
                                except Exception as e:
                                    logger.warning("Transcription with %s failed: %s", model_name, e)
                            return ""
                            
                        transcription = await asyncio.to_thread(transcribe_audio, audio_bytes)
                        if transcription:
                            content = transcription
                            logger.info("Voice transcribed successfully: %s", content)
                        else:
                            content = "[Ovozli xabarni matnga aylantirib bo'lmadi]"
                    except Exception as e:
                        logger.exception("Voice processing failed")
                        content = f"[Ovozli xabarni qayta ishlashda xatolik: {str(e)}]"

                cm = ChannelMessage(
                    channel="telegram",
                    sender=str(msg.from_user.id) if msg.from_user else "",
                    content=content,
                    message_id=str(msg.message_id),
                    conversation_id=str(msg.chat.id),
                )
                logger.info(
                    "Incoming telegram message: %s from %s, chat_id=%s, chat_type=%s, chat_title=%s",
                    cm.content,
                    cm.sender,
                    cm.conversation_id,
                    msg.chat.type,
                    msg.chat.title or "N/A",
                )

                # Guruh/superguruhlarda bot faqat o'zi tilga olinganda javob berishi kerak
                if msg.chat.type in ("group", "supergroup"):
                    bot_info = await context.bot.get_me()
                    bot_username = f"@{bot_info.username}"
                    
                    is_reply_to_bot = (
                        msg.reply_to_message
                        and msg.reply_to_message.from_user
                        and msg.reply_to_message.from_user.id == bot_info.id
                    )
                    has_mention = (
                        bot_username.lower() in content.lower()
                        or "jarvis" in content.lower()
                        or "aisha" in content.lower()
                    )
                    is_command = content.startswith("/")
                    
                    if not (is_reply_to_bot or has_mention or is_command):
                        logger.info("Ignoring message in group chat because bot is not mentioned or replied to.")
                        return

                # Enforce allow-list when configured
                if self._allowed_chat_ids:
                    _allowed = {
                        cid.strip()
                        for cid in self._allowed_chat_ids.split(",")
                        if cid.strip()
                    }
                    if cm.conversation_id not in _allowed:
                        logger.info(
                            "Ignoring message from unlisted chat %s. Allowed: %s",
                            cm.conversation_id,
                            self._allowed_chat_ids,
                        )
                        return

                for handler in self._handlers:
                    try:
                        handler(cm)
                    except Exception:
                        logger.exception("Telegram handler error")
                if self._bus is not None:
                    self._bus.publish(
                        EventType.CHANNEL_MESSAGE_RECEIVED,
                        {
                            "channel": cm.channel,
                            "sender": cm.sender,
                            "content": cm.content,
                            "message_id": cm.message_id,
                        },
                    )

            app.add_handler(MessageHandler(filters.TEXT | filters.VOICE, _handle_msg))
            app.run_polling(stop_signals=None, drop_pending_updates=True)
        except Exception:
            logger.debug("Telegram poll loop error", exc_info=True)
            self._status = ChannelStatus.ERROR

    def _publish_sent(self, channel: str, content: str, conversation_id: str) -> None:
        """Publish a CHANNEL_MESSAGE_SENT event on the bus."""
        if self._bus is not None:
            self._bus.publish(
                EventType.CHANNEL_MESSAGE_SENT,
                {
                    "channel": channel,
                    "content": content,
                    "conversation_id": conversation_id,
                },
            )


__all__ = ["TelegramChannel"]
