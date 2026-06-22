import os
import logging
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import Update, InputMediaPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SIGNATURE = (
    "\n👤 С уважением, эксперт мухаммаднур по недвижимости\n"
    "https://t.me/Tower_estate"
)


async def districts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        result = supabase.table("districts").select("*").execute()
        if not result.data:
            await update.message.reply_text("Districts jadvali bo'sh.")
            return
        lines = [f"{d.get('id')}: {d.get('name')}" for d in result.data]
        text = "\n".join(lines)
        for i in range(0, len(text), 3500):
            await update.message.reply_text(text[i:i+3500])
    except Exception as e:
        await update.message.reply_text(f"Xato: {e}")


import json
import asyncio
import httpx

EDGE_FUNCTION_URL = f"{SUPABASE_URL}/functions/v1/bulk-import-properties"
IMPORT_SECRET = os.getenv("IMPORT_SECRET", "")
BATCH_SIZE = 500


async def import_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or args[0] != IMPORT_SECRET or not IMPORT_SECRET:
        await update.message.reply_text("Ruxsat yo'q.")
        return

    try:
        with open("properties_import.json", encoding="utf-8") as f:
            all_rows = json.load(f)
    except Exception as e:
        await update.message.reply_text(f"Faylni o'qishda xato: {e}")
        return

    total = len(all_rows)
    await update.message.reply_text(f"Import boshlandi: {total} ta qator, {BATCH_SIZE} tadan bo'lib yuborilmoqda...")

    inserted_total = 0
    error_total = 0
    error_samples = []

    async with httpx.AsyncClient(timeout=60) as client:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "apikey": SUPABASE_KEY,
        }
        for i in range(0, total, BATCH_SIZE):
            batch = all_rows[i : i + BATCH_SIZE]
            try:
                resp = await client.post(
                    EDGE_FUNCTION_URL,
                    headers=headers,
                    json={"properties": batch},
                )
                data = resp.json()
                inserted_total += data.get("inserted_count", 0)
                error_total += data.get("error_count", 0)
                for err in data.get("errors", [])[:3]:
                    if len(error_samples) < 10:
                        error_samples.append(str(err))
            except Exception as e:
                error_total += len(batch)
                error_samples.append(f"Batch {i}: {e}")

            batch_num = i // BATCH_SIZE + 1
            total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
            if batch_num % 5 == 0 or batch_num == total_batches:
                await update.message.reply_text(
                    f"Progress: {batch_num}/{total_batches} batch | "
                    f"OK: {inserted_total} | Xato: {error_total}"
                )
            await asyncio.sleep(0.3)

    summary = f"✅ Import tugadi.\nMuvaffaqiyatli: {inserted_total}\nXato: {error_total}"
    if error_samples:
        summary += "\n\nNamuna xatolar:\n" + "\n".join(error_samples[:5])
    await update.message.reply_text(summary)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Uy ID raqamini yuboring, men sizga ma'lumotlarini chiqarib beraman.\n"
        "Masalan: 6"
    )


def build_caption(property_id: int, p: dict) -> str:
    district_name = "-"
    if p.get("districts"):
        district_name = p["districts"].get("name", "-")

    address = p.get("address") or "-"
    landmark = p.get("landmark") or "-"
    rooms = p.get("rooms") or "-"

    floor_raw = p.get("floor") or "-"
    if "/" in str(floor_raw):
        floor_cur, floor_total = str(floor_raw).split("/", 1)
    else:
        floor_cur, floor_total = floor_raw, "-"

    area = p.get("area")
    area_text = f"{area}м2" if area else "-"

    price = p.get("price")
    price_text = f"{price} y.e/ месяц" if price else "Договорная"

    deposit = p.get("deposit")
    deposit_text = f"${deposit}" if deposit else "Договорная"

    caption = (
        f"🆔 {property_id}\n"
        f"📍Район: {district_name}\n"
        f"🎯Адрес: {address}\n"
        f"📌Ориентир: {landmark}\n\n"
        f"🌆Комнат: {rooms}\n"
        f"🔼Этаж: {floor_cur}\n"
        f"⏫Этажность: {floor_total}\n"
        f"📐Площадь: {area_text}\n"
        f"💸Цена: {price_text}\n"
        f"💸Депозит: {deposit_text}\n"
        f"{SIGNATURE}"
    )
    return caption


async def handle_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text(
            "Iltimos, faqat uy ID raqamini yuboring (masalan: 6)"
        )
        return

    property_id = int(text)

    try:
        result = (
            supabase.table("properties")
            .select("*, districts(name)")
            .eq("id", property_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"Supabase error: {e}")
        await update.message.reply_text(
            "⚠️ Bazaga ulanishda xatolik. Keyinroq urinib ko'ring."
        )
        return

    if not result.data:
        await update.message.reply_text(f"❌ #{property_id} ID'li uy topilmadi.")
        return

    p = result.data[0]
    caption = build_caption(property_id, p)

    raw_images = p.get("images") or []
    images = []
    if raw_images:
        try:
            signed = supabase.storage.from_("property-images").create_signed_urls(
                raw_images, 3600
            )
            logger.info(f"raw_images: {raw_images}")
            logger.info(f"signed result: {signed}")
            for item in signed:
                url = (
                    item.get("signedURL")
                    or item.get("signedUrl")
                    or item.get("signed_url")
                )
                if url:
                    if url.startswith("/"):
                        url = f"{SUPABASE_URL}{url}"
                    images.append(url)
        except Exception as e:
            logger.error(f"Signed URL error: {e}")

    if images:
        try:
            media = [InputMediaPhoto(media=url) for url in images[:10]]
            media[0] = InputMediaPhoto(media=images[0], caption=caption)
            await update.message.reply_media_group(media=media)
        except Exception as e:
            logger.error(f"Media group error: {e}")
            await update.message.reply_text(
                caption + "\n\n⚠️ (Rasmlarni yuklashda xatolik bo'ldi)"
            )
    else:
        await update.message.reply_text(caption)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("districts", districts_cmd))
    app.add_handler(CommandHandler("import", import_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_id))
    logger.info("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
