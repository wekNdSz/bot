import asyncio, logging, os
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update

# ── Config ────────────────────────────────────────────────────────────────────
TOKEN      = os.getenv("BOT_TOKEN", "8895842204:AAGg3NVgJkCg7I6OVD_-Nkr7DfD4VxZw1jQ")
WEBHOOK_HOST = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")  # авто Railway
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL  = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}" if WEBHOOK_HOST else ""

MAX_MSG = 4096

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()

# Хранилище последних профилей (в памяти, для демо)
profiles: dict[int, dict] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_date(ts) -> str:
    if not ts:
        return "неизвестно"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def parse_gifts(gifts_raw) -> list[dict]:
    result = []
    for g in (gifts_raw or []):
        gift = g.gift
        result.append({
            "id":        getattr(gift, "id", "—"),
            "stars":     getattr(gift, "star_count", "?"),
            "text":      getattr(g,    "text", None) or "—",
            "sender_id": g.sender_user.id        if g.sender_user else "аноним",
            "sender":    g.sender_user.full_name if g.sender_user else "Аноним",
            "date":      fmt_date(getattr(g, "send_date", None)),
            "private":   getattr(g, "is_private", False),
        })
    return result


# ── Bot handler ───────────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user    = message.from_user
    chat_id = user.id

    full_info = None
    try:
        full_info = await bot.get_chat(chat_id)
    except Exception as e:
        log.warning(f"getChat: {e}")

    gifts_raw = None
    try:
        resp = await bot.get_user_gifts(user_id=chat_id)
        gifts_raw = resp.gifts if resp and resp.gifts else None
    except Exception as e:
        log.warning(f"getUserGifts: {e}")

    photos = None
    try:
        photos = await bot.get_user_profile_photos(chat_id, limit=1)
    except Exception as e:
        log.warning(f"getPhotos: {e}")

    # Аватарка — получаем file_url
    avatar_url = None
    if photos and photos.total_count > 0:
        file_id   = photos.photos[0][-1].file_id
        file_info = await bot.get_file(file_id)
        avatar_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        await message.answer_photo(photo=file_id)

    name_parts = [user.first_name]
    if user.last_name:
        name_parts.append(user.last_name)

    profile = {
        "id":          user.id,
        "full_name":   " ".join(name_parts),
        "username":    user.username,
        "language":    user.language_code or "—",
        "premium":     bool(user.is_premium),
        "is_bot":      user.is_bot,
        "verified":    getattr(user, "is_verified",  False),
        "restricted":  getattr(user, "is_restricted", False),
        "scam":        getattr(user, "is_scam",  False),
        "fake":        getattr(user, "is_fake",  False),
        "reg_date":    fmt_date(getattr(full_info, "date", None)),
        "avatar_url":  avatar_url,
        "gifts":       parse_gifts(gifts_raw),
    }
    profiles[chat_id] = profile

    # Отвечаем в Telegram
    link = f'https://{WEBHOOK_HOST}/profile/{chat_id}' if WEBHOOK_HOST else "(запусти локально)"
    gift_count = len(profile["gifts"])
    text = (
        f"👋 <b>Hello, {profile['full_name']}!</b>\n\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"⭐ Premium: {'✅' if profile['premium'] else '❌'}\n"
        f"🎁 Подарков: {gift_count}\n\n"
        f"🌐 <b>Web-профиль:</b>\n{link}"
    )
    await message.answer(text)

    # Если подарков много — шлём списком отдельно
    gift_lines = []
    for g in profile["gifts"]:
        gift_lines.append(
            f"┌ 🎀 <b>{g['id']}</b>\n"
            f"├ Цена: {g['stars']} ⭐\n"
            f"├ Подпись: {g['text']}\n"
            f"├ От: <code>{g['sender_id']}</code> ({g['sender']})\n"
            f"├ Дата: {g['date']}\n"
            f"└ Приватный: {'🔒 да' if g['private'] else 'нет'}"
        )

    if gift_lines:
        chunk = "🎁 <b>Gifts:</b>\n\n"
        for line in gift_lines:
            candidate = chunk + line + "\n\n"
            if len(candidate) > MAX_MSG:
                await message.answer(chunk.strip())
                chunk = line + "\n\n"
            else:
                chunk = candidate
        if chunk.strip():
            await message.answer(chunk.strip())


# ── FastAPI ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)
        log.info(f"Webhook set: {WEBHOOK_URL}")
    else:
        asyncio.create_task(dp.start_polling(bot))
        log.info("Polling started (no RAILWAY_PUBLIC_DOMAIN)")
    yield
    await bot.session.close()

app = FastAPI(lifespan=lifespan)


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data   = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def index():
    cards = ""
    for uid, p in profiles.items():
        cards += f"""
        <a href="/profile/{uid}" class="card">
          <img src="{p['avatar_url'] or 'https://via.placeholder.com/64'}" alt="avatar">
          <div>
            <b>{p['full_name']}</b><br>
            <span>@{p['username'] or '—'}</span><br>
            <span>ID: {uid}</span>
          </div>
        </a>"""
    if not cards:
        cards = "<p class='empty'>Нет профилей. Напиши боту /start в Telegram.</p>"

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>InformationAboutYou</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',sans-serif;background:#0f0f13;color:#e8e8f0;min-height:100vh}}
  header{{background:linear-gradient(135deg,#2563eb,#7c3aed);padding:24px 32px;display:flex;align-items:center;gap:12px}}
  header h1{{font-size:1.4rem;font-weight:700}}
  header span{{font-size:1.8rem}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px;padding:32px}}
  .card{{background:#1a1a24;border:1px solid #2a2a3a;border-radius:16px;padding:20px;display:flex;gap:14px;align-items:center;text-decoration:none;color:inherit;transition:transform .2s,border-color .2s}}
  .card:hover{{transform:translateY(-3px);border-color:#7c3aed}}
  .card img{{width:56px;height:56px;border-radius:50%;object-fit:cover;border:2px solid #7c3aed}}
  .card b{{font-size:1rem;color:#fff}}
  .card span{{font-size:.82rem;color:#9090b0}}
  .empty{{text-align:center;color:#666;padding:80px 20px;grid-column:1/-1;font-size:1.1rem}}
</style>
</head>
<body>
<header><span>🤖</span><h1>InformationAboutYou — профили</h1></header>
<div class="grid">{cards}</div>
</body></html>""")


@app.get("/profile/{user_id}", response_class=HTMLResponse)
async def profile_page(user_id: int):
    p = profiles.get(user_id)
    if not p:
        return HTMLResponse("<h2 style='font-family:sans-serif;padding:40px'>Профиль не найден. Напиши /start боту.</h2>", status_code=404)

    def badge(val, yes="✅", no="❌"):
        return yes if val else no

    gifts_html = ""
    for g in p["gifts"]:
        priv = "🔒 Приватный" if g["private"] else ""
        gifts_html += f"""
        <div class="gift-card">
          <div class="gift-header">🎀 <b>{g['id']}</b> {priv}</div>
          <div class="gift-row"><span>Цена</span><b>{g['stars']} ⭐</b></div>
          <div class="gift-row"><span>Подпись</span><i>{g['text']}</i></div>
          <div class="gift-row"><span>От</span>{g['sender']} <code>#{g['sender_id']}</code></div>
          <div class="gift-row"><span>Дата</span>{g['date']}</div>
        </div>"""

    if not gifts_html:
        gifts_html = "<p class='no-gifts'>Нет подарков или скрыты</p>"

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{p['full_name']} — профиль</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',sans-serif;background:#0f0f13;color:#e8e8f0;min-height:100vh;padding-bottom:60px}}
  header{{background:linear-gradient(135deg,#2563eb,#7c3aed);padding:20px 28px;display:flex;align-items:center;gap:12px}}
  header a{{color:#fff;text-decoration:none;font-size:.9rem;opacity:.8}}
  .hero{{display:flex;align-items:center;gap:24px;padding:32px;background:#14141e;border-bottom:1px solid #2a2a3a}}
  .hero img{{width:96px;height:96px;border-radius:50%;border:3px solid #7c3aed;object-fit:cover}}
  .hero h2{{font-size:1.5rem;color:#fff}}
  .hero .sub{{color:#9090b0;margin-top:4px;font-size:.9rem}}
  .hero .username{{color:#7c3aed;font-weight:600}}
  .body{{max-width:760px;margin:0 auto;padding:28px 20px}}
  .section{{margin-bottom:28px}}
  .section h3{{font-size:1rem;color:#7c7c9c;text-transform:uppercase;letter-spacing:.06em;margin-bottom:14px}}
  .info-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
  .info-item{{background:#1a1a24;border:1px solid #2a2a3a;border-radius:12px;padding:14px 16px}}
  .info-item .label{{font-size:.75rem;color:#7c7c9c;margin-bottom:4px}}
  .info-item .value{{font-size:1rem;font-weight:600}}
  .badge-yes{{color:#22c55e}}.badge-no{{color:#ef4444}}.badge-warn{{color:#f59e0b}}
  .gift-card{{background:#1a1a24;border:1px solid #2a2a3a;border-radius:14px;padding:16px;margin-bottom:12px}}
  .gift-header{{font-size:1rem;margin-bottom:10px;color:#c084fc}}
  .gift-row{{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #22223a;font-size:.88rem}}
  .gift-row:last-child{{border:none}}
  .gift-row span{{color:#7c7c9c}}
  .gift-row code{{background:#22223a;padding:1px 5px;border-radius:4px;font-size:.8rem}}
  .no-gifts{{color:#555;font-style:italic;padding:20px 0}}
  @media(max-width:500px){{.info-grid{{grid-template-columns:1fr}}.hero{{flex-direction:column;text-align:center}}}}
</style>
</head>
<body>
<header><a href="/">← Все профили</a></header>
<div class="hero">
  <img src="{p['avatar_url'] or 'https://via.placeholder.com/96'}" alt="avatar">
  <div>
    <h2>{p['full_name']}</h2>
    <div class="sub">
      {'<span class="username">@' + p['username'] + '</span>' if p['username'] else ''}
      {'⭐ Premium' if p['premium'] else ''}
    </div>
    <div class="sub" style="margin-top:6px">ID: <code style="color:#c084fc">{p['id']}</code></div>
  </div>
</div>
<div class="body">
  <div class="section">
    <h3>Основная информация</h3>
    <div class="info-grid">
      <div class="info-item"><div class="label">Язык</div><div class="value">{p['language']}</div></div>
      <div class="info-item"><div class="label">Дата регистрации</div><div class="value" style="font-size:.85rem">{p['reg_date']}</div></div>
      <div class="info-item"><div class="label">Premium</div><div class="value {'badge-yes' if p['premium'] else 'badge-no'}">{'✅ Да' if p['premium'] else '❌ Нет'}</div></div>
      <div class="info-item"><div class="label">Верифицирован</div><div class="value {'badge-yes' if p['verified'] else 'badge-no'}">{badge(p['verified'])}</div></div>
      <div class="info-item"><div class="label">Ограничен</div><div class="value {'badge-warn' if p['restricted'] else 'badge-yes'}">{badge(p['restricted'], '⚠️ Да', '✅ Нет')}</div></div>
      <div class="info-item"><div class="label">Scam / Fake</div><div class="value {'badge-no' if p['scam'] or p['fake'] else 'badge-yes'}">{badge(p['scam'] or p['fake'], '🚨 Да', '✅ Нет')}</div></div>
    </div>
  </div>
  <div class="section">
    <h3>Подарки ({len(p['gifts'])})</h3>
    {gifts_html}
  </div>
</div>
</body></html>""")


@app.get("/api/profile/{user_id}")
async def api_profile(user_id: int):
    p = profiles.get(user_id)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(p)
