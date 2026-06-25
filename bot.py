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

SIGNATURE_TEMPLATE = (
    "\n👤 С уважением, эксперт {name} по недвижимости\n"
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
BATCH_SIZE = 100


async def exportids_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or args[0] != IMPORT_SECRET or not IMPORT_SECRET:
        await update.message.reply_text("Ruxsat yo'q.")
        return

    await update.message.reply_text("Eksport boshlandi...")
    all_rows = []
    page_size = 1000
    offset = 0
    try:
        while True:
            result = (
                supabase.table("properties")
                .select("id, address, owner_phone")
                .order("id")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            if not result.data:
                break
            all_rows.extend(result.data)
            if len(result.data) < page_size:
                break
            offset += page_size
    except Exception as e:
        await update.message.reply_text(f"Xato: {e}")
        return

    with open("export_ids.json", "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False)

    await update.message.reply_document(
        document=open("export_ids.json", "rb"),
        filename="export_ids.json",
        caption=f"Jami: {len(all_rows)} qator",
    )


OLD_SERVER_BASE = "http://201.51.18.2/rieltor/uploads/"


async def fetchimages_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or args[0] != IMPORT_SECRET or not IMPORT_SECRET:
        await update.message.reply_text("Ruxsat yo'q.")
        return

    start_index = int(args[1]) if len(args) > 1 else 0

    import glob
    import urllib.parse

    part_files = sorted(glob.glob("image_manifest_part*.json"))
    if part_files:
        all_items = []
        for pf in part_files:
            with open(pf, encoding="utf-8") as f:
                all_items.extend(json.load(f))
    else:
        try:
            with open("image_manifest.json", encoding="utf-8") as f:
                all_items = json.load(f)
        except Exception as e:
            await update.message.reply_text(f"Faylni o'qishda xato: {e}")
            return

    all_items = all_items[start_index:]
    total = len(all_items)
    await update.message.reply_text(
        f"Rasm yuklash boshlandi (#{start_index}dan): {total} ta rasm..."
    )

    url = f"{SUPABASE_URL}/functions/v1/bulk-fetch-images"
    ok_total = 0
    error_total = 0
    error_samples = []
    batch_size = 120

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "apikey": SUPABASE_KEY,
        }
        for i in range(0, total, batch_size):
            batch = all_items[i : i + batch_size]
            items = [
                {
                    "property_id": it["new_id"],
                    "filename": it["filename"],
                    "source_url": OLD_SERVER_BASE
                    + urllib.parse.quote(it["filename"]),
                }
                for it in batch
            ]
            try:
                resp = await client.post(url, headers=headers, json={"items": items})
                if resp.status_code != 200:
                    error_total += len(batch)
                    error_samples.append(
                        f"Batch {start_index+i}: HTTP {resp.status_code} - {resp.text[:300]}"
                    )
                    continue
                data = resp.json()
                for r in data.get("results", []):
                    if r.get("ok"):
                        ok_total += 1
                    else:
                        error_total += 1
                        if len(error_samples) < 10:
                            error_samples.append(str(r))
            except Exception as e:
                error_total += len(batch)
                error_samples.append(
                    f"Batch {start_index+i}: {type(e).__name__}: {repr(e)}"
                )

            done = i + len(batch)
            if (i // batch_size) % 5 == 0 or done >= total:
                await update.message.reply_text(
                    f"Progress: {start_index+done}/{start_index+total} | "
                    f"OK: {ok_total} | Xato: {error_total}\n"
                    f"Davom ettirish uchun: /fetchimages {IMPORT_SECRET} {start_index+done}"
                )
            await asyncio.sleep(0.2)

    summary = f"✅ Rasm yuklash tugadi.\nOK: {ok_total}\nXato: {error_total}"
    if error_samples:
        summary += "\n\n" + "\n".join(error_samples[:5])
    await update.message.reply_text(summary)


async def pushmanifest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or args[0] != IMPORT_SECRET or not IMPORT_SECRET:
        await update.message.reply_text("Ruxsat yo'q.")
        return

    import glob
    part_files = sorted(glob.glob("image_manifest_part*.json"))
    if part_files:
        all_items = []
        for pf in part_files:
            with open(pf, encoding="utf-8") as f:
                all_items.extend(json.load(f))
    else:
        try:
            with open("image_manifest.json", encoding="utf-8") as f:
                all_items = json.load(f)
        except Exception as e:
            await update.message.reply_text(f"Faylni o'qishda xato: {e}")
            return

    total = len(all_items)
    await update.message.reply_text(f"Manifest yuklash boshlandi: {total} ta yozuv...")

    url = f"{SUPABASE_URL}/functions/v1/bulk-import-manifest"
    inserted_total = 0
    error_total = 0
    error_samples = []
    batch_size = 500

    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "apikey": SUPABASE_KEY,
        }
        for i in range(0, total, batch_size):
            batch = all_items[i : i + batch_size]
            try:
                resp = await client.post(url, headers=headers, json={"items": batch})
                if resp.status_code != 200:
                    error_total += len(batch)
                    error_samples.append(f"Batch {i}: HTTP {resp.status_code} - {resp.text[:300]}")
                    continue
                inserted_total += len(batch)
            except Exception as e:
                error_total += len(batch)
                error_samples.append(f"Batch {i}: {type(e).__name__}: {repr(e)}")

            batch_num = i // batch_size + 1
            total_batches = (total + batch_size - 1) // batch_size
            if batch_num % 10 == 0 or batch_num == total_batches:
                await update.message.reply_text(
                    f"Progress: {batch_num}/{total_batches} | OK: {inserted_total} | Xato: {error_total}"
                )
            await asyncio.sleep(0.2)

    summary = f"✅ Manifest yuklash tugadi.\nOK: {inserted_total}\nXato: {error_total}"
    if error_samples:
        summary += "\n\n" + "\n".join(error_samples[:5])
    await update.message.reply_text(summary)


async def import_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or args[0] != IMPORT_SECRET or not IMPORT_SECRET:
        await update.message.reply_text("Ruxsat yo'q.")
        return

    start_index = 0
    if len(args) > 1:
        try:
            start_index = int(args[1])
        except ValueError:
            start_index = 0

    try:
        with open("properties_import.json", encoding="utf-8") as f:
            all_rows = json.load(f)
    except Exception as e:
        await update.message.reply_text(f"Faylni o'qishda xato: {e}")
        return

    all_rows = all_rows[start_index:]
    total = len(all_rows)
    await update.message.reply_text(
        f"Import boshlandi (#{start_index}dan): {total} ta qator, {BATCH_SIZE} tadan bo'lib yuborilmoqda..."
    )

    inserted_total = 0
    error_total = 0
    error_samples = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
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
                logger.info(f"Batch {i}: status={resp.status_code}")
                if resp.status_code != 200:
                    error_total += len(batch)
                    snippet = resp.text[:300]
                    error_samples.append(f"Batch {i}: HTTP {resp.status_code} - {snippet}")
                    logger.error(f"Batch {i} failed: {resp.status_code} {resp.text[:1000]}")
                    continue
                data = resp.json()
                inserted_total += data.get("inserted_count", 0)
                error_total += data.get("error_count", 0)
                for err in data.get("errors", [])[:3]:
                    if len(error_samples) < 10:
                        error_samples.append(str(err))
            except Exception as e:
                error_total += len(batch)
                error_samples.append(f"Batch {i}: {type(e).__name__}: {repr(e)}")
                logger.error(f"Batch {i} exception: {type(e).__name__}: {repr(e)}")

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


def build_caption(property_id: int, p: dict, employee_name: str) -> str:
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
        f"{SIGNATURE_TEMPLATE.format(name=employee_name)}"
    )
    return caption


async def handle_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text(
            "Iltimos, faqat uy tartib raqamini yuboring (masalan: 6)"
        )
        return

    display_number = int(text)
    offset = display_number - 1

    try:
        result = (
            supabase.table("properties")
            .select("*, districts(name)")
            .order("id")
            .range(offset, offset)
            .execute()
        )
    except Exception as e:
        logger.error(f"Supabase error: {e}")
        await update.message.reply_text(
            "⚠️ Bazaga ulanishda xatolik. Keyinroq urinib ko'ring."
        )
        return

    if not result.data:
        await update.message.reply_text(f"❌ #{display_number} ID'li uy topilmadi.")
        return

    p = result.data[0]

    employee_name = "Tower Estate"
    added_by = p.get("added_by")
    if added_by:
        try:
            prof = (
                supabase.table("profiles")
                .select("full_name")
                .eq("id", added_by)
                .execute()
            )
            if prof.data and prof.data[0].get("full_name"):
                employee_name = prof.data[0]["full_name"]
        except Exception as e:
            logger.error(f"Profile fetch error: {e}")

    caption = build_caption(display_number, p, employee_name)

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

    owner_name = p.get("owner_name") or "-"
    owner_phone = p.get("owner_phone") or "-"
    owner_caption = f"📞 Egasi: {owner_name}\n📱 Telefon: {owner_phone}"
    await update.message.reply_text(owner_caption)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("districts", districts_cmd))
    app.add_handler(CommandHandler("import", import_cmd))
    app.add_handler(CommandHandler("exportids", exportids_cmd))
    app.add_handler(CommandHandler("pushmanifest", pushmanifest_cmd))
    app.add_handler(CommandHandler("fetchimages", fetchimages_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_id))
    logger.info("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
