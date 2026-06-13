#!/usr/bin/env python3
"""OpenJarvis Advanced Telegram Agent System — Aisha.

Ushbu script foydalanuvchining shaxsiy Telegram botini:
  1. Google Calendar (Uchrashuvlarni ko'rish, yangi voqealar qo'shish va fon rejimida eslatish).
  2. Gmail (Xatlarni tekshirish va javob yuborish).
  3. Notion (Sahifalar qidirish, o'qish va yangi sahifalar yaratish).
  4. Telegram guruhlari / chatlari bilan to'liq bog'laydi.
  5. Suhbat xotirasini saqlaydi (conversation memory).

Foydalanish:
    python examples/telegram_agent_system.py
"""

from __future__ import annotations

import os
import sys
import time
import json
import logging
import threading
from collections import deque
from datetime import datetime, timedelta
from typing import Any
import httpx

# Loyiha src papkasini importlar uchun yo'lga qo'shamiz
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

try:
    from openjarvis import Jarvis
    from openjarvis.channels.telegram import TelegramChannel
    from openjarvis.tools._stubs import BaseTool, ToolSpec
    from openjarvis.core.types import ToolResult
    from openjarvis.core.registry import ToolRegistry
except ImportError as exc:
    print(
        "Xatolik: OpenJarvis yuklanmadi. Iltimos, virtual muhitni yoqing.",
        file=sys.stderr
    )
    sys.exit(1)

# Loglarni sozlaymiz
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("telegram_agent_system")

# ===========================================================================
# XOTIRA TIZIMI (Conversation Memory per Chat)
# ===========================================================================

# Har bir chat uchun oxirgi N ta xabarni saqlaydigan lug'at
# { chat_id: deque([{"role": "user"/"assistant", "content": "..."},...]) }
MEMORY_MAX_MESSAGES = 20
_chat_memories: dict[str, deque] = {}
_MEMORY_DIR = os.path.join(os.path.dirname(__file__), ".chat_memory")
os.makedirs(_MEMORY_DIR, exist_ok=True)


def _memory_file(chat_id: str) -> str:
    return os.path.join(_MEMORY_DIR, f"{chat_id}.json")


def load_memory(chat_id: str) -> deque:
    """Disk-dan xotira faylini yuklaydi."""
    path = _memory_file(chat_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return deque(data, maxlen=MEMORY_MAX_MESSAGES)
        except Exception:
            pass
    return deque(maxlen=MEMORY_MAX_MESSAGES)


def save_memory(chat_id: str, memory: deque) -> None:
    """Xotirani disk-ga saqlaydi."""
    try:
        path = _memory_file(chat_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(list(memory), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Xotirani saqlashda xatolik: %s", e)


def get_memory(chat_id: str) -> deque:
    """Xotirani oladi (kesh yoki disk-dan)."""
    if chat_id not in _chat_memories:
        _chat_memories[chat_id] = load_memory(chat_id)
    return _chat_memories[chat_id]


def add_to_memory(chat_id: str, role: str, content: str) -> None:
    """Xotiraga yangi xabar qo'shadi va diskka saqlaydi."""
    mem = get_memory(chat_id)
    mem.append({"role": role, "content": content})
    save_memory(chat_id, mem)


def build_context_prompt(chat_id: str, new_message: str) -> str:
    """Suhbat tarixi + yangi xabardan to'liq kontekst promptini yig'adi."""
    mem = get_memory(chat_id)

    now_uz = datetime.now()

    system_prompt = f"""Sen mening shaxsiy AI yordamchim — "Aisha"san.
Sen mening ikkinchi miyam, shaxsiy kotibim, biznes yordamchim va bilim bazamsan.
Sening vazifang mening ishlarimni boshqarish, ma'lumotlarni eslab qolish, rejalashtirish, tahlil qilish va qaror qabul qilishimga yordam berishdir.

Hozirgi vaqt (Toshkent, UTC+5): {now_uz.strftime('%Y-yil, %d-%B, soat %H:%M')}

# ASOSIY VAZIFALAR
- Kalendarni boshqarish (voqealarni qo'shish, ko'rish, o'chirish, eslatish)
- Vazifalarni kuzatish va eslatmalar yaratish
- Gmail (yangi xatlarni tekshirish, javob yuborish)
- Notion (eslatmalar, sahifalar boshqaruvi)
- Loyiha va hamkorlarni nazorat qilish
- Internetdan ma'lumot topish
- Kitob va hujjatlarni tahlil qilish
- Muhim ma'lumotlarni xotirada saqlash

# XOTIRA
Foydalanuvchining loyihalari, maqsadlari, hamkorlari, uchrashuvlari, qiziqishlari va odatlari haqidagi muhim ma'lumotlarni eslab qol va keyingi suhbatlarda ulardan foydalan.

# MULOQOT USLUBI
- Har doim O'ZBEK tilida javob ber
- Qisqa, aniq va tabiiy gapir
- Do'stona va professional bo'l
- Keraksiz uzun javob berma
- Faqat savolga javob bermasdan, agar foydali bo'lsa taklif va eslatmalar ham ber

# QAROR QABUL QILISH
Qaror kerak bo'lsa: variantlarni solishtir, afzallik/kamchiliklarni ko'rsat, eng maqbulini tavsiya qil.

# OVOZLI REJIM
Agar ovozli xabar transkripsiya qilinib kelsa — uni oddiy matnli buyruq sifatida qabul qil va bajara boshlagan ko'rinishda javob ber.

# KALENDARNI O'CHIRISH QOIDALARI
Agar foydalanuvchi biror uchrashuv yoki voqeani o'chirishni so'rasa (masalan: "4-sini o'chir", "stolichniy montajni o'chir" va hokazo):
1. Hech qachon event_id ga tartib raqam yoki taxminiy qiymat yozma (masalan, event_id="4" deb yozish MUTLAQO XATO).
2. Har doim birinchi navbatda `calendar_list_events` asbobini chaqirib, bugungi yoki tegishli kungi rejalar ro'yxatini ol.
3. Ro'yxatdagi tadbir nomiga qarab uning haqiqiy unikal ID-sini (masalan: `31j85p...`) aniqlab ol.
4. Keyin o'sha haqiqiy ID bilan `calendar_delete_event` asbobini chaqir.

# MAQSAD
Foydalanuvchining vaqtini tejash, ish unumdorligini oshirish va muhim narsalarni nazorat ostida ushlash."""


    history_lines = []
    for turn in mem:
        prefix = "👤 Foydalanuvchi" if turn["role"] == "user" else "🤖 Aisha"
        history_lines.append(f"{prefix}: {turn['content']}")

    history_text = "\n".join(history_lines) if history_lines else "(suhbat boshlanmoqda)"

    full_prompt = (
        f"{system_prompt}\n"
        f"--- Suhbat Tarixi ---\n"
        f"{history_text}\n"
        f"---\n"
        f"👤 Foydalanuvchi: {new_message}\n"
        f"🤖 Aisha:"
    )
    return full_prompt


# ===========================================================================
# CUSTOM OPENJARVIS TOOLS (Kengaytirilgan Yozish Asboblari)
# ===========================================================================

class CreateCalendarEventTool(BaseTool):
    """Google Calendar-ga yangi uchrashuv yoki dedlayn qo'shish."""

    tool_id = "calendar_create_event"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calendar_create_event",
            description=(
                "Google Calendar-ga yangi uchrashuv, voqea yoki dedlayn qo'shadi. "
                "Boshlanish va tugash vaqti ISO 8601 formatida (masalan: '2026-05-30T15:00:00') bo'lishi lozim. "
                "Hozirgi vaqt zonasi: Asia/Tashkent (UTC+5). Agar foydalanuvchi aniq yil ko'rsatmasa, "
                "joriy yilni ishlat."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Uchrashuv yoki dedlayn nomi/mavzusi"},
                    "start_time": {"type": "string", "description": "Boshlanish vaqti (ISO 8601 formatida, e.g. YYYY-MM-DDTHH:MM:SS)"},
                    "end_time": {"type": "string", "description": "Tugash vaqti (ISO 8601 formatida)"},
                    "location": {"type": "string", "description": "Uchrashuv manzili (ixtiyoriy)"},
                    "description": {"type": "string", "description": "Uchrashuv tavsifi (ixtiyoriy)"},
                },
                "required": ["summary", "start_time", "end_time"]
            },
            category="productivity"
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            from openjarvis.connectors.gcalendar import GCalendarConnector
            conn = GCalendarConnector()
            if not conn.is_connected():
                return ToolResult(self.spec.name, "Xatolik: Google Calendar ulangan emas!", False)

            token = conn._get_token()
            summary = params.get("summary")
            start_time = params.get("start_time")
            end_time = params.get("end_time")
            location = params.get("location", "")
            description = params.get("description", "")

            url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            body = {
                "summary": summary,
                "location": location,
                "description": description,
                "start": {"dateTime": start_time, "timeZone": "Asia/Tashkent"},
                "end": {"dateTime": end_time, "timeZone": "Asia/Tashkent"},
            }
            resp = httpx.post(url, headers=headers, json=body, timeout=15.0)
            if resp.status_code < 300:
                event_data = resp.json()
                event_link = event_data.get("htmlLink", "")
                return ToolResult(
                    self.spec.name,
                    f"✅ Google Calendar-ga qo'shildi: '{summary}' ({start_time} — {end_time})\n🔗 {event_link}",
                    True
                )
            return ToolResult(self.spec.name, f"Google API xatoligi: {resp.text}", False)
        except Exception as e:
            return ToolResult(self.spec.name, f"Xatolik: {str(e)}", False)


class ListCalendarEventsTool(BaseTool):
    """Google Calendar-dan bugungi yoki ko'rsatilgan kunning voqealarini ko'rish."""

    tool_id = "calendar_list_events"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calendar_list_events",
            description=(
                "Google Calendar-dan ma'lum bir sananing yoki bugungi kunning barcha voqealarini ko'rsatadi. "
                "Foydalanuvchi 'bugungi uchrashuvlarim', 'ertangi rejalar' yoki shunga o'xshash narsa so'raganda ishlatiladi."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Qidirilayotgan sana (YYYY-MM-DD formatida). Bo'sh qoldirilsa bugun qidiriladi."
                    },
                },
                "required": []
            },
            category="productivity"
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            from openjarvis.connectors.gcalendar import GCalendarConnector
            conn = GCalendarConnector()
            if not conn.is_connected():
                return ToolResult(self.spec.name, "Xatolik: Google Calendar ulangan emas!", False)

            token = conn._get_token()
            date_str = params.get("date", "")

            if date_str:
                try:
                    target = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    target = datetime.now()
            else:
                target = datetime.now()

            day_start = target.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = target.replace(hour=23, minute=59, second=59, microsecond=0)

            # Toshkent UTC+5 offset
            tz_offset = "+05:00"
            time_min = day_start.strftime(f"%Y-%m-%dT%H:%M:%S{tz_offset}")
            time_max = day_end.strftime(f"%Y-%m-%dT%H:%M:%S{tz_offset}")

            url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
            headers = {"Authorization": f"Bearer {token}"}
            resp = httpx.get(url, headers=headers, params={
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 20,
            }, timeout=15.0)

            if resp.status_code >= 300:
                return ToolResult(self.spec.name, f"Google API xatoligi: {resp.text}", False)

            items = resp.json().get("items", [])
            if not items:
                return ToolResult(
                    self.spec.name,
                    f"📅 {target.strftime('%d-%B-%Y')} kuni hech qanday uchrashuv yoki voqea topilmadi.",
                    True
                )

            lines = [f"📅 *{target.strftime('%d-%B-%Y')} kungi rejalar:*\n"]
            for i, item in enumerate(items, 1):
                title = item.get("summary", "(nomsiz)")
                evt_id = item.get("id", "")
                start = item.get("start", {})
                start_dt = start.get("dateTime", start.get("date", ""))
                if "T" in start_dt:
                    try:
                        dt = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
                        time_label = dt.strftime("%H:%M")
                    except Exception:
                        time_label = start_dt
                else:
                    time_label = "Kun bo'yi"
                location = item.get("location", "")
                loc_str = f" | 📍 {location}" if location else ""
                lines.append(f"{i}. 🕒 {time_label} — *{title}*{loc_str} (ID: {evt_id})")

            return ToolResult(self.spec.name, "\n".join(lines), True)
        except Exception as e:
            return ToolResult(self.spec.name, f"Xatolik: {str(e)}", False)


class CheckUnreadGmailTool(BaseTool):
    """Gmail-dan o'qilmagan xatlarni tekshirish."""

    tool_id = "gmail_check_unread"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="gmail_check_unread",
            description="Gmail qutisidagi o'qilmagan xatlarni ko'rsatadi. 'Yangi pochta bormi?' kabi so'rovlar uchun ishlatiladi.",
            parameters={"type": "object", "properties": {}, "required": []},
            category="communication"
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            from openjarvis.connectors.gmail import GmailConnector
            conn = GmailConnector()
            if not conn.is_connected():
                return ToolResult(self.spec.name, "Xatolik: Gmail ulangan emas!", False)

            token = conn._refresh_token()
            url = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
            headers = {"Authorization": f"Bearer {token}"}
            resp = httpx.get(url, headers=headers, params={
                "q": "is:unread",
                "maxResults": 5,
            }, timeout=15.0)

            if resp.status_code >= 300:
                return ToolResult(self.spec.name, f"Gmail API xatoligi: {resp.text}", False)

            messages = resp.json().get("messages", [])
            if not messages:
                return ToolResult(self.spec.name, "📭 Yangi o'qilmagan xat yo'q.", True)

            result_lines = [f"📬 *{len(messages)} ta o'qilmagan xat topildi:*\n"]
            for msg_ref in messages[:5]:
                msg_resp = httpx.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_ref['id']}",
                    headers=headers,
                    params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                    timeout=10.0
                )
                if msg_resp.status_code < 300:
                    headers_list = msg_resp.json().get("payload", {}).get("headers", [])
                    header_dict = {h["name"]: h["value"] for h in headers_list}
                    sender = header_dict.get("From", "Noma'lum")
                    subject = header_dict.get("Subject", "(mavzusiz)")
                    result_lines.append(f"• ✉️ *{subject}*\n  👤 {sender}")

            return ToolResult(self.spec.name, "\n".join(result_lines), True)
        except Exception as e:
            return ToolResult(self.spec.name, f"Xatolik: {str(e)}", False)


class SendGmailTool(BaseTool):
    """Gmail orqali yangi xat yoki javob yuborish."""

    tool_id = "gmail_send"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="gmail_send",
            description="Gmail orqali ko'rsatilgan manzilga yangi xat yoki javob yuboradi.",
            parameters={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Qabul qiluvchining email manzili"},
                    "subject": {"type": "string", "description": "Xat mavzusi"},
                    "body": {"type": "string", "description": "Xat matni/mazmuni"}
                },
                "required": ["to", "subject", "body"]
            },
            category="communication"
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            from openjarvis.connectors.gmail import GmailConnector
            conn = GmailConnector()
            if not conn.is_connected():
                return ToolResult(self.spec.name, "Xatolik: Gmail ulangan emas!", False)

            token = conn._refresh_token()
            to = params.get("to")
            subject = params.get("subject")
            body = params.get("body")

            import base64
            from email.mime.text import MIMEText

            message = MIMEText(body)
            message['to'] = to
            message['subject'] = subject
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

            url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            resp = httpx.post(url, headers=headers, json={"raw": raw}, timeout=15.0)
            if resp.status_code < 300:
                return ToolResult(self.spec.name, f"✅ Pochta muvaffaqiyatli yuborildi.\n📧 Kimga: {to}\n📌 Mavzu: {subject}", True)
            return ToolResult(self.spec.name, f"Gmail API xatoligi: {resp.text}", False)
        except Exception as e:
            return ToolResult(self.spec.name, f"Xatolik: {str(e)}", False)


class CreateNotionPageTool(BaseTool):
    """Notion-da yangi sahifa yoki qayd yaratish."""

    tool_id = "notion_create_page"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="notion_create_page",
            description="Notion ma'lumotlar bazasi yoki sahifasi ichida yangi sahifa/qayd yaratadi.",
            parameters={
                "type": "object",
                "properties": {
                    "parent_page_id": {"type": "string", "description": "Notion sahifasining UUID identifikatori (parent page ID)"},
                    "title": {"type": "string", "description": "Yangi sahifa sarlavhasi"},
                    "content": {"type": "string", "description": "Sahifa ichidagi matn mazmuni"}
                },
                "required": ["parent_page_id", "title", "content"]
            },
            category="knowledge"
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            from openjarvis.connectors.notion import NotionConnector
            conn = NotionConnector()
            token = conn._resolve_token()
            if not token:
                return ToolResult(self.spec.name, "Xatolik: Notion ulangan emas!", False)

            parent_page_id = params.get("parent_page_id")
            title = params.get("title")
            content = params.get("content")

            url = "https://api.notion.com/v1/pages"
            headers = {
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json"
            }
            body = {
                "parent": {"page_id": parent_page_id},
                "properties": {
                    "title": {"title": [{"text": {"content": title}}]}
                },
                "children": [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {"rich_text": [{"text": {"content": content}}]}
                    }
                ]
            }
            resp = httpx.post(url, headers=headers, json=body, timeout=15.0)
            if resp.status_code < 300:
                return ToolResult(self.spec.name, f"✅ Notion-da yangi sahifa yaratildi: '{title}'", True)
            return ToolResult(self.spec.name, f"Notion API xatoligi: {resp.text}", False)
        except Exception as e:
            return ToolResult(self.spec.name, f"Xatolik: {str(e)}", False)


class DeleteCalendarEventTool(BaseTool):
    """Google Calendar-dan uchrashuv yoki voqeani o'chirib tashlash."""

    tool_id = "calendar_delete_event"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calendar_delete_event",
            description=(
                "Google Calendar-dan uchrashuv yoki voqeani o'chirib tashlaydi. "
                "Buning uchun uchrashuvning event_id (identifikatori) va calendar_id sini uzatish kerak. "
                "Agar calendar_id uzatilmasa, u 'primary' (asosiy taqvim) deb hisoblanadi."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "O'chiriladigan voqeaning unikal ID-si"},
                    "calendar_id": {"type": "string", "description": "Taqvim identifikatori (ixtiyoriy, default: primary)"}
                },
                "required": ["event_id"]
            },
            category="productivity"
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            from openjarvis.connectors.gcalendar import GCalendarConnector
            conn = GCalendarConnector()
            if not conn.is_connected():
                return ToolResult(self.spec.name, "Xatolik: Google Calendar ulangan emas!", False)

            token = conn._get_token()
            event_id = params.get("event_id")
            calendar_id = params.get("calendar_id", "primary")

            url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}"
            headers = {"Authorization": f"Bearer {token}"}
            resp = httpx.delete(url, headers=headers, timeout=15.0)
            if resp.status_code == 204 or resp.status_code < 300:
                return ToolResult(self.spec.name, f"✅ Voqea muvaffaqiyatli o'chirildi (ID: {event_id})", True)
            return ToolResult(self.spec.name, f"Google API xatoligi: {resp.text}", False)
        except Exception as e:
            return ToolResult(self.spec.name, f"Xatolik: {str(e)}", False)


# Ro'yxatdan o'tkazamiz
ToolRegistry.register("calendar_create_event")(CreateCalendarEventTool)
ToolRegistry.register("calendar_list_events")(ListCalendarEventsTool)
ToolRegistry.register("calendar_delete_event")(DeleteCalendarEventTool)
ToolRegistry.register("gmail_check_unread")(CheckUnreadGmailTool)
ToolRegistry.register("gmail_send")(SendGmailTool)
ToolRegistry.register("notion_create_page")(CreateNotionPageTool)


# ===========================================================================
# FON REJIMIDA TAQVIMNI KUZATISH VA Reminders
# ===========================================================================

# Eslatilgan uchrashuvlarni saqlash fayli
NOTIFIED_EVENTS_FILE = os.path.join(os.path.dirname(__file__), ".chat_memory", "notified_events.json")

def load_notified_events() -> set[str]:
    if os.path.exists(NOTIFIED_EVENTS_FILE):
        try:
            with open(NOTIFIED_EVENTS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_notified_event(event_id: str):
    notified = load_notified_events()
    notified.add(event_id)
    try:
        with open(NOTIFIED_EVENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(notified), f, ensure_ascii=False)
    except Exception as e:
        logger.warning("Eslatmalarni saqlashda xatolik: %s", e)

def run_calendar_reminders(telegram_channel: TelegramChannel, allowed_chat_id: str):
    """Har 15 daqiqada Google Calendar-ni tekshiradi va uchrashuvlarni eslatadi (takrorlanishlarsiz)."""
    logger.info("Calendar Reminders fonda ishga tushdi.")
    from openjarvis.connectors.gcalendar import GCalendarConnector

    while True:
        try:
            conn = GCalendarConnector()
            if conn.is_connected() and allowed_chat_id:
                token = conn._get_token()
                now = datetime.now()
                
                # Toshkent UTC+5 offset
                tz_offset = "+05:00"
                # Kelgusi 45 daqiqa ichidagi voqealarni olamiz
                time_min = now.strftime(f"%Y-%m-%dT%H:%M:%S{tz_offset}")
                time_max = (now + timedelta(minutes=45)).strftime(f"%Y-%m-%dT%H:%M:%S{tz_offset}")

                url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
                headers = {"Authorization": f"Bearer {token}"}
                resp = httpx.get(url, headers=headers, params={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": 10,
                }, timeout=15.0)

                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    notified = load_notified_events()

                    for item in items:
                        event_id = item.get("id")
                        if not event_id:
                            continue

                        # Agar oldin eslatilgan bo'lsa tashlab ketamiz
                        if event_id in notified:
                            continue

                        summary = item.get("summary", "(nomsiz)")
                        start = item.get("start", {})
                        start_dt = start.get("dateTime", start.get("date", ""))
                        
                        if not start_dt or "T" not in start_dt:
                            continue

                        try:
                            # Vaqtni formatlaymiz
                            dt = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
                            # Toshkent vaqtiga o'tkazish
                            dt_naive = dt.replace(tzinfo=None)
                            time_diff = dt_naive - now.replace(tzinfo=None)
                            
                            # Faqat 0 dan 30 daqiqagacha bo'lgan kelajakdagi voqealarni eslatamiz
                            if timedelta(seconds=0) <= time_diff <= timedelta(minutes=30):
                                organizer = item.get("organizer", {})
                                author = organizer.get("displayName") or organizer.get("email", "Noma'lum")
                                
                                reminder_text = (
                                    f"🔔 *Yaqinda uchrashuv boshlanadi!*\n\n"
                                    f"📅 *Mavzu*: {summary}\n"
                                    f"🕒 *Vaqt*: {dt_naive.strftime('%H:%M')} ({_time_left_str(time_diff)})\n"
                                    f"👤 *Tashkilotchi*: {author}\n"
                                )
                                telegram_channel.send(
                                    channel="telegram",
                                    content=reminder_text,
                                    conversation_id=allowed_chat_id
                                )
                                save_notified_event(event_id)
                            elif time_diff < timedelta(seconds=0):
                                # Agar uchrashuv o'tib ketgan bo'lsa, ro'yxatga kiritamiz
                                save_notified_event(event_id)
                        except Exception as parse_err:
                            logger.error("Vaqt tahlilida xato: %s", parse_err)
        except Exception as err:
            logger.error("Reminders xatoligi: %s", err)

        time.sleep(600)  # Har 10 daqiqada tekshiradi (kamroq yuklama va tezroq aniqlash)





def _time_left_str(diff: timedelta) -> str:
    mins = int(diff.total_seconds() // 60)
    if mins <= 0:
        return "hozir boshlanadi"
    return f"{mins} daqiqadan so'ng"


# ===========================================================================
# ASOSIY PROGRAMMA LOGIKASI
# ===========================================================================

def main():
    # Railway/Cloud uchun ulagichlar (Google, Notion) sozlamalarini muhit o'zgaruvchilaridan tiklaymiz
    connectors_dir = os.path.expanduser("~/.openjarvis/connectors")
    os.makedirs(connectors_dir, exist_ok=True)
    for key in ["google", "gcalendar", "gcontacts", "gdrive", "gmail", "google_tasks", "notion"]:
        env_val = os.getenv(f"GPAYLOAD_{key.upper()}")
        if env_val:
            try:
                json_data = json.loads(env_val)
                file_path = os.path.join(connectors_dir, f"{key}.json")
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(json_data, f, ensure_ascii=False, indent=2)
                logger.info(f"Ulagich muvaffaqiyatli tiklandi: {key}")
            except Exception as e:
                logger.error(f"Ulagichni tiklashda xato ({key}): {e}")

    # API kalitlarini muhit o'zgaruvchilariga avtomatik qo'shamiz
    if not os.getenv("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = "AQ.Ab8RN6Le1wFSIKPURvChMuqU7zXVvZpVwNXR_6HmlPzKVbQ-vg"
    if not os.getenv("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = "AQ.Ab8RN6Le1wFSIKPURvChMuqU7zXVvZpVwNXR_6HmlPzKVbQ-vg"

    model = os.getenv("JARVIS_MODEL", "gemini-2.5-flash")
    engine = os.getenv("JARVIS_ENGINE", "cloud")

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "8324627610:AAF3D3KCVwb5QjrRzEonIAz2W2fmrhK8R-w")
    allowed_chat_ids = os.getenv("ALLOWED_CHAT_IDS", "8835179633")

    if not os.getenv("NOTION_TOKEN"):
        os.environ["NOTION_TOKEN"] = "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"

    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN topilmadi!")
        sys.exit(1)

    logger.info("Aisha Assistant Server ishga tushmoqda...")

    # Asboblar ro'yxati
    enabled_tools = [
        "think",
        "digest_collect",
        "calendar_create_event",
        "calendar_list_events",
        "calendar_delete_event",
        "gmail_check_unread",
        "gmail_send",
        "notion_create_page",
        "web_search"
    ]

    try:
        j = Jarvis(model=model, engine_key=engine)
        j_fallback = Jarvis(model="gemini-2.5-flash-lite", engine_key=engine)
    except Exception as exc:
        logger.error("Jarvis-ni yuklashda xatolik: %s", exc)
        sys.exit(1)

    telegram_channel = TelegramChannel(
        bot_token=bot_token,
        allowed_chat_ids=allowed_chat_ids
    )

    # Kiruvchi xabarlar uchun handler
    def handle_message(msg):
        chat_id = msg.conversation_id
        user_text = msg.content
        logger.info("Xabar qabul qilindi (Chat ID: %s): %s", chat_id, user_text)

        # Foydalanuvchi xabarini xotiraga qo'shamiz
        add_to_memory(chat_id, "user", user_text)

        # Xotira konteksti bilan to'liq prompt
        full_prompt = build_context_prompt(chat_id, user_text)

        try:
            try:
                response = j.ask(
                    full_prompt,
                    agent="orchestrator",
                    tools=enabled_tools,
                    temperature=0.4
                )
            except Exception as primary_err:
                err_str = str(primary_err).upper()
                if "503" in err_str or "UNAVAILABLE" in err_str or "HIGH DEMAND" in err_str or "429" in err_str:
                    logger.warning("Primary model unavailable, trying fallback...")
                    response = j_fallback.ask(
                        full_prompt,
                        agent="orchestrator",
                        tools=enabled_tools,
                        temperature=0.4
                    )
                else:
                    raise primary_err

            # Bot javobini xotiraga qo'shamiz
            add_to_memory(chat_id, "assistant", response)

            telegram_channel.send(
                channel="telegram",
                content=response,
                conversation_id=chat_id
            )
            logger.info("Javob yuborildi.")
        except Exception as err:
            logger.exception("Xabarni bajarishda xatolik:")
            telegram_channel.send(
                channel="telegram",
                content=f"❌ *Xatolik yuz berdi:*\n`{str(err)}`",
                conversation_id=chat_id
            )

    telegram_channel.on_message(handle_message)
    telegram_channel.connect()

    # Eslatgichlar oqimini ishga tushiramiz
    primary_chat_id = allowed_chat_ids.split(",")[0].strip() if allowed_chat_ids else ""
    if primary_chat_id:
        reminder_thread = threading.Thread(
            target=run_calendar_reminders,
            args=(telegram_channel, primary_chat_id),
            daemon=True
        )
        reminder_thread.start()

    logger.info("Aisha Telegram Agent muvaffaqiyatli ishlamoqda!")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Tizim to'xtatilmoqda...")
        telegram_channel.disconnect()
        j.close()
        j_fallback.close()


if __name__ == "__main__":
    main()
