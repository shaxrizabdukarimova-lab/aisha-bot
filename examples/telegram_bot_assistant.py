#!/usr/bin/env python3
"""OpenJarvis Telegram Bot Assistant.

Ushbu script OpenJarvis-ning aqlli orchestrator agentini Telegram botga ulaydi.
Kelgan har bir xabarni Jarvis mahalliy til modeli yordamida qayta ishlaydi va javob beradi.

Foydalanish:
    1. Telegram-da @BotFather orqali yangi bot yarating va TOKEN oling.
    2. .env fayliga yoki tizimga quyidagi o'zgaruvchilarni qo'shing:
       $env:TELEGRAM_BOT_TOKEN="sizning_bot_tokeningiz"
       $env:ALLOWED_CHAT_IDS="chat_id_1,chat_id_2"  (Xavfsizlik uchun faqat o'zingizga ruxsat bering)
    3. Scriptni ishga tushiring:
       python examples/telegram_bot_assistant.py
"""

from __future__ import annotations

import os
import sys
import time
import logging

# Loyiha src papkasini importlar uchun yo'lga qo'shamiz
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

try:
    from openjarvis import Jarvis
    from openjarvis.channels.telegram import TelegramChannel
except ImportError as exc:
    print(
        "Xatolik: OpenJarvis kutubxonalari yuklanmadi. "
        "Avval virtual muhitni yoqing va barcha bog'liqliklarni o'rnating.",
        file=sys.stderr
    )
    sys.exit(1)

# Loglarni sozlaymiz
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("telegram_bot_assistant")

def main():
    # Model va Engine sozlamalari
    model = os.getenv("JARVIS_MODEL", "qwen3:8b")
    engine = os.getenv("JARVIS_ENGINE", "ollama")
    
    # Telegram Bot Token va Xavfsizlik bo'yicha ruxsat etilgan chat ID-lar
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    allowed_chat_ids = os.getenv("ALLOWED_CHAT_IDS", "")
    
    if not bot_token:
        logger.error(
            "\n" + "="*80 + "\n"
            "XATOLIK: TELEGRAM_BOT_TOKEN atrof-muhit o'zgaruvchisi topilmadi!\n\n"
            "Iltimos, bot tokeningizni quyidagi buyruq orqali sozlang:\n"
            "  PowerShell: $env:TELEGRAM_BOT_TOKEN=\"SIZNING_BOT_TOKEN\"\n"
            "  CMD: set TELEGRAM_BOT_TOKEN=\"SIZNING_BOT_TOKEN\"\n"
            "  Linux/macOS: export TELEGRAM_BOT_TOKEN=\"SIZNING_BOT_TOKEN\"\n"
            "="*80
        )
        sys.exit(1)

    logger.info("OpenJarvis yuklanmoqda... Engine: %s, Model: %s", engine, model)
    try:
        j = Jarvis(model=model, engine_key=engine)
    except Exception as exc:
        logger.error(
            "Jarvis-ni ishga tushirib bo'lmadi. Mahalliy modelingiz (masalan, Ollama) "
            "ishlayotganiga va sozlanganiga ishonch hosil qiling.\nXatolik tafsiloti: %s", exc
        )
        sys.exit(1)

    logger.info("Telegram kanal adapteri yaratilmoqda...")
    channel = TelegramChannel(
        bot_token=bot_token,
        allowed_chat_ids=allowed_chat_ids
    )

    # Kiruvchi xabarlarni qayta ishlaydigan callback funksiya
    def handle_incoming_message(msg):
        logger.info("Xabar keldi (Chat ID: %s, Sender: %s): %s", msg.conversation_id, msg.sender, msg.content)
        
        # Foydalanuvchiga bot o'ylayotganligini ko'rsatish
        channel.send(
            channel="telegram",
            content="🔄 *O'ylayapman...*",
            conversation_id=msg.conversation_id
        )

        try:
            # Jarvis orchestrator agenti orqali javob olish (hot-triage)
            response = j.ask(
                msg.content,
                agent="orchestrator",
                tools=["think", "memory_store", "memory_search"],
                temperature=0.3
            )
            
            # Agar javob juda uzun bo'lsa yoki formati Markdown bo'lsa, to'g'ridan-to'g'ri yuboradi
            channel.send(
                channel="telegram",
                content=response,
                conversation_id=msg.conversation_id
            )
            logger.info("Javob yuborildi.")
            
        except Exception as err:
            logger.exception("Xabarni qayta ishlashda xatolik yuz berdi:")
            channel.send(
                channel="telegram",
                content=f"❌ *Kechirasiz, xatolik yuz berdi:*\n`{str(err)}`",
                conversation_id=msg.conversation_id
            )

    # Callback funksiyani ro'yxatdan o'tkazamiz
    channel.on_message(handle_incoming_message)

    logger.info("Telegram botga ulanmoqda...")
    channel.connect()

    logger.info("Telegram bot muvaffaqiyatli ishga tushdi! (To'xtatish uchun: Ctrl+C)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Bot to'xtatilmoqda...")
        channel.disconnect()
        j.close()
        logger.info("Bot to'liq to'xtadi.")

if __name__ == "__main__":
    main()
