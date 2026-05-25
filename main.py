import asyncio, logging, os
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

TOKEN        = os.getenv("BOT_TOKEN", "8895842204:AAGg3NVgJkCg7I6OVD_-Nkr7DfD4VxZw1jQ")
WEBHOOK_HOST = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL  = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}" if WEBHOOK_HOST else ""
WEB_URL      = f"https://{WEBHOOK_HOST}" if WEBHOOK_HOST else "http://localhost:8000"
MAX_MSG      = 4096

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()
profiles: dict[int, dict] = {}


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
            "text":      getattr(g, "text", None) or "—",
            "sender_id": g.sender_user.id        if g.sender_user else "аноним",
            "sender":    g.sender_user.full_name if g.sender_user else "Аноним",
            "date":      fmt_date(getattr(g, "send_date", None)),
            "private":   getattr(g, "is_private", False),
        })
    return result


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

    avatar_url = None
    if photos and photos.total_count > 0:
        file_id   = photos.photos[0][-1].file_id
        file_info = await bot.get_file(file_id)
        avatar_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"

    name_parts = [user.first_name]
    if user.last_name:
        name_parts.append(user.last_name)

    profile = {
        "id":         user.id,
        "full_name":  " ".join(name_parts),
        "username":   user.username,
        "language":   user.language_code or "—",
        "premium":    bool(user.is_premium),
        "is_bot":     user.is_bot,
        "verified":   getattr(user, "is_verified",   False),
        "restricted": getattr(user, "is_restricted", False),
        "scam":       getattr(user, "is_scam",  False),
        "fake":       getattr(user, "is_fake",  False),
        "reg_date":   fmt_date(getattr(full_info, "date", None)),
        "avatar_url": avatar_url,
        "gifts":      parse_gifts(gifts_raw),
    }
    profiles[chat_id] = profile

    # Mini Web App кнопка — открывает сразу на профиль пользователя
    webapp_url = f"{WEB_URL}/app?uid={chat_id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🌐 Открыть профиль",
            web_app=WebAppInfo(url=webapp_url)
        )
    ]])

    await message.answer(
        f"👋 Привет, <b>{profile['full_name']}</b>!\nТвой профиль готов:",
        reply_markup=kb
    )

    # Подарки в чат (разбивка)
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
        log.info(f"Webhook: {WEBHOOK_URL}")
    else:
        asyncio.create_task(dp.start_polling(bot))
        log.info("Polling started")
    yield
    await bot.session.close()

app = FastAPI(lifespan=lifespan)


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data   = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}


@app.get("/api/profiles")
async def api_profiles():
    return JSONResponse(list(profiles.values()))


@app.get("/api/profile/{user_id}")
async def api_profile(user_id: int):
    p = profiles.get(user_id)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(p)


@app.get("/app", response_class=HTMLResponse)
async def mini_app(uid: int = 0):
    return HTMLResponse(get_app_html(uid))


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(get_app_html(0))


def get_app_html(initial_uid: int) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>InfoAboutYou</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;600;700&family=Google+Sans+Display:wght@400;700&display=swap" rel="stylesheet">
<style>
:root {{
  --md-sys-color-primary: #6750A4;
  --md-sys-color-on-primary: #FFFFFF;
  --md-sys-color-primary-container: #EADDFF;
  --md-sys-color-on-primary-container: #21005D;
  --md-sys-color-secondary: #625B71;
  --md-sys-color-secondary-container: #E8DEF8;
  --md-sys-color-surface: #FFFBFE;
  --md-sys-color-surface-variant: #E7E0EC;
  --md-sys-color-on-surface: #1C1B1F;
  --md-sys-color-on-surface-variant: #49454F;
  --md-sys-color-outline: #79747E;
  --md-sys-color-outline-variant: #CAC4D0;
  --md-sys-color-background: #FFFBFE;
  --md-sys-color-error: #B3261E;
  --md-sys-color-success: #386A20;
  --md-elevation-1: 0 1px 2px rgba(0,0,0,.08),0 2px 6px rgba(0,0,0,.06);
  --md-elevation-2: 0 1px 2px rgba(0,0,0,.1),0 4px 8px rgba(0,0,0,.08);
  --md-elevation-3: 0 4px 8px rgba(0,0,0,.1),0 8px 16px rgba(0,0,0,.08);
  --radius-xs: 8px; --radius-s: 12px; --radius-m: 16px; --radius-l: 24px; --radius-xl: 28px;
  --font: 'Google Sans', sans-serif;
}}
@media(prefers-color-scheme:dark) {{
  :root {{
    --md-sys-color-primary: #D0BCFF;
    --md-sys-color-on-primary: #381E72;
    --md-sys-color-primary-container: #4F378B;
    --md-sys-color-on-primary-container: #EADDFF;
    --md-sys-color-secondary: #CCC2DC;
    --md-sys-color-secondary-container: #4A4458;
    --md-sys-color-surface: #1C1B1F;
    --md-sys-color-surface-variant: #49454F;
    --md-sys-color-on-surface: #E6E1E5;
    --md-sys-color-on-surface-variant: #CAC4D0;
    --md-sys-color-outline: #938F99;
    --md-sys-color-outline-variant: #49454F;
    --md-sys-color-background: #1C1B1F;
    --md-sys-color-error: #F2B8B5;
    --md-sys-color-success: #A8D5A2;
  }}
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }}
body {{
  font-family: var(--font);
  background: var(--md-sys-color-background);
  color: var(--md-sys-color-on-surface);
  min-height: 100dvh;
  overflow-x: hidden;
}}

/* Navigation Bar */
.nav-bar {{
  position: fixed; bottom: 0; left: 0; right: 0; z-index: 100;
  background: var(--md-sys-color-surface);
  border-top: 1px solid var(--md-sys-color-outline-variant);
  display: flex;
  padding: 0 0 env(safe-area-inset-bottom);
  box-shadow: 0 -1px 3px rgba(0,0,0,.06);
}}
.nav-item {{
  flex: 1; display: flex; flex-direction: column; align-items: center;
  padding: 12px 0 10px; gap: 4px; cursor: pointer; border: none;
  background: transparent; color: var(--md-sys-color-on-surface-variant);
  font-family: var(--font); font-size: 12px; transition: color .2s;
  position: relative;
}}
.nav-item.active {{ color: var(--md-sys-color-primary); }}
.nav-indicator {{
  position: absolute; top: 6px; width: 64px; height: 32px;
  background: var(--md-sys-color-secondary-container);
  border-radius: 16px; opacity: 0; transform: scaleX(.6);
  transition: opacity .2s, transform .2s;
}}
.nav-item.active .nav-indicator {{ opacity: 1; transform: scaleX(1); }}
.nav-icon {{ font-size: 22px; position: relative; z-index: 1; }}
.nav-label {{ position: relative; z-index: 1; font-weight: 500; }}

/* Screens */
.screen {{ display: none; flex-direction: column; min-height: 100dvh; padding-bottom: 80px; }}
.screen.active {{ display: flex; }}

/* Top App Bar */
.top-bar {{
  position: sticky; top: 0; z-index: 50;
  background: var(--md-sys-color-surface);
  padding: 16px 16px 8px;
  display: flex; align-items: center; gap: 12px;
}}
.top-bar h1 {{
  font-family: 'Google Sans Display', sans-serif;
  font-size: 22px; font-weight: 400;
  color: var(--md-sys-color-on-surface); flex: 1;
}}

/* Search bar */
.search-bar {{
  margin: 8px 16px 16px;
  display: flex; align-items: center; gap: 12px;
  background: var(--md-sys-color-surface-variant);
  border-radius: 28px; padding: 10px 20px;
  box-shadow: var(--md-elevation-1);
}}
.search-bar input {{
  flex: 1; border: none; background: transparent;
  font-family: var(--font); font-size: 16px;
  color: var(--md-sys-color-on-surface); outline: none;
}}
.search-bar input::placeholder {{ color: var(--md-sys-color-on-surface-variant); }}
.search-icon {{ font-size: 20px; color: var(--md-sys-color-on-surface-variant); }}

/* Profile Hero */
.profile-hero {{
  margin: 0 16px 16px;
  background: linear-gradient(135deg, var(--md-sys-color-primary-container), var(--md-sys-color-secondary-container));
  border-radius: var(--radius-xl);
  padding: 28px 24px;
  display: flex; align-items: center; gap: 20px;
  box-shadow: var(--md-elevation-2);
  animation: slideUp .4s ease;
}}
@keyframes slideUp {{ from {{ opacity:0; transform:translateY(20px) }} to {{ opacity:1; transform:translateY(0) }} }}
.avatar-wrap {{ position: relative; flex-shrink: 0; }}
.avatar {{
  width: 80px; height: 80px; border-radius: 50%; object-fit: cover;
  border: 3px solid var(--md-sys-color-primary);
  background: var(--md-sys-color-surface-variant);
}}
.avatar-placeholder {{
  width: 80px; height: 80px; border-radius: 50%;
  background: var(--md-sys-color-primary);
  display: flex; align-items: center; justify-content: center;
  font-size: 32px; color: var(--md-sys-color-on-primary); font-weight: 700;
}}
.premium-badge {{
  position: absolute; bottom: -2px; right: -2px;
  background: #FFB800; border-radius: 50%; width: 24px; height: 24px;
  display: flex; align-items: center; justify-content: center; font-size: 13px;
  border: 2px solid var(--md-sys-color-surface);
}}
.hero-info {{ flex: 1; min-width: 0; }}
.hero-name {{
  font-size: 20px; font-weight: 700;
  color: var(--md-sys-color-on-primary-container);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.hero-username {{
  font-size: 14px; color: var(--md-sys-color-primary);
  margin-top: 2px; font-weight: 500;
}}
.hero-id {{
  font-size: 12px; color: var(--md-sys-color-on-surface-variant);
  margin-top: 6px; font-family: monospace;
}}

/* Info Cards Grid */
.cards-grid {{
  display: grid; grid-template-columns: 1fr 1fr;
  gap: 12px; margin: 0 16px 16px;
}}
.info-card {{
  background: var(--md-sys-color-surface);
  border-radius: var(--radius-m);
  padding: 16px; box-shadow: var(--md-elevation-1);
  animation: slideUp .4s ease both;
}}
.info-card .label {{
  font-size: 11px; font-weight: 600; letter-spacing: .06em;
  text-transform: uppercase; color: var(--md-sys-color-on-surface-variant);
  margin-bottom: 6px;
}}
.info-card .value {{
  font-size: 15px; font-weight: 600; color: var(--md-sys-color-on-surface);
}}
.value.yes {{ color: var(--md-sys-color-success); }}
.value.no  {{ color: var(--md-sys-color-error); }}
.value.warn {{ color: #E65100; }}

/* Section title */
.section-title {{
  font-size: 14px; font-weight: 600; letter-spacing: .04em;
  color: var(--md-sys-color-on-surface-variant);
  text-transform: uppercase;
  margin: 0 16px 10px; margin-top: 8px;
}}

/* Gift Cards */
.gift-card {{
  margin: 0 16px 12px;
  background: var(--md-sys-color-surface);
  border-radius: var(--radius-l);
  box-shadow: var(--md-elevation-1);
  overflow: hidden;
  animation: slideUp .4s ease both;
}}
.gift-header {{
  background: var(--md-sys-color-primary-container);
  padding: 12px 16px;
  display: flex; align-items: center; gap: 10px;
}}
.gift-header .gift-name {{
  font-weight: 700; color: var(--md-sys-color-on-primary-container); font-size: 15px; flex: 1;
}}
.gift-stars {{
  background: var(--md-sys-color-primary);
  color: var(--md-sys-color-on-primary);
  border-radius: 20px; padding: 3px 10px;
  font-size: 13px; font-weight: 700;
}}
.gift-body {{ padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }}
.gift-row {{
  display: flex; justify-content: space-between; align-items: center;
  font-size: 13px;
}}
.gift-row .gr-label {{ color: var(--md-sys-color-on-surface-variant); }}
.gift-row .gr-val {{ font-weight: 500; color: var(--md-sys-color-on-surface); text-align: right; max-width: 60%; }}
.gift-private {{
  background: var(--md-sys-color-secondary-container);
  color: var(--md-sys-color-on-surface-variant);
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
}}

/* People list */
.people-list {{ padding: 0 16px; display: flex; flex-direction: column; gap: 10px; }}
.person-card {{
  background: var(--md-sys-color-surface);
  border-radius: var(--radius-m);
  padding: 14px 16px;
  display: flex; align-items: center; gap: 14px;
  box-shadow: var(--md-elevation-1);
  cursor: pointer; transition: box-shadow .2s;
  animation: slideUp .3s ease both;
}}
.person-card:active {{ box-shadow: var(--md-elevation-2); }}
.person-avatar {{
  width: 48px; height: 48px; border-radius: 50%; object-fit: cover;
  background: var(--md-sys-color-primary);
  display: flex; align-items: center; justify-content: center;
  font-size: 18px; font-weight: 700; color: var(--md-sys-color-on-primary);
  flex-shrink: 0;
}}
.person-info {{ flex: 1; min-width: 0; }}
.person-name {{ font-weight: 600; font-size: 15px; color: var(--md-sys-color-on-surface); }}
.person-sub  {{ font-size: 12px; color: var(--md-sys-color-on-surface-variant); margin-top: 2px; }}
.person-chip {{
  background: var(--md-sys-color-primary-container);
  color: var(--md-sys-color-on-primary-container);
  border-radius: 12px; padding: 4px 10px; font-size: 11px; font-weight: 600;
}}

/* FAB */
.fab {{
  position: fixed; right: 20px; bottom: 88px;
  width: 56px; height: 56px; border-radius: 16px;
  background: var(--md-sys-color-primary-container);
  color: var(--md-sys-color-on-primary-container);
  border: none; font-size: 24px; cursor: pointer;
  box-shadow: var(--md-elevation-3);
  display: flex; align-items: center; justify-content: center;
  transition: transform .2s;
}}
.fab:active {{ transform: scale(.94); }}

/* Empty state */
.empty {{
  text-align: center; padding: 60px 20px;
  color: var(--md-sys-color-on-surface-variant);
}}
.empty-icon {{ font-size: 64px; margin-bottom: 16px; }}
.empty-text {{ font-size: 16px; font-weight: 500; }}
.empty-sub  {{ font-size: 14px; margin-top: 6px; opacity: .7; }}

/* Loading */
.loader {{
  display: flex; justify-content: center; padding: 40px;
}}
.spinner {{
  width: 40px; height: 40px; border: 3px solid var(--md-sys-color-outline-variant);
  border-top-color: var(--md-sys-color-primary);
  border-radius: 50%; animation: spin .8s linear infinite;
}}
@keyframes spin {{ to {{ transform: rotate(360deg) }} }}
</style>
</head>
<body>

<!-- Screen: My Profile -->
<div class="screen active" id="screen-me">
  <div class="top-bar">
    <h1>Мой профиль</h1>
    <span id="refresh-btn" style="font-size:22px;cursor:pointer" onclick="loadMe()">🔄</span>
  </div>
  <div id="me-content"><div class="loader"><div class="spinner"></div></div></div>
</div>

<!-- Screen: Search -->
<div class="screen" id="screen-search">
  <div class="top-bar"><h1>Поиск</h1></div>
  <div class="search-bar">
    <span class="search-icon">🔍</span>
    <input type="text" id="search-input" placeholder="Имя или @username…" oninput="filterPeople()">
  </div>
  <div id="search-results" class="people-list"></div>
</div>

<!-- Nav -->
<nav class="nav-bar">
  <button class="nav-item active" onclick="switchTab('me',this)">
    <div class="nav-indicator"></div>
    <span class="nav-icon">👤</span>
    <span class="nav-label">Профиль</span>
  </button>
  <button class="nav-item" onclick="switchTab('search',this)">
    <div class="nav-indicator"></div>
    <span class="nav-icon">🔍</span>
    <span class="nav-label">Поиск</span>
  </button>
</nav>

<script>
const INITIAL_UID = {initial_uid};
let allProfiles = [];
let myProfile = null;

// ── Tab switching ──────────────────────────────────────────────────────────
function switchTab(name, btn) {{
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  document.getElementById('screen-' + name).classList.add('active');
  btn.classList.add('active');
  if (name === 'search') loadPeople();
}}

// ── Load my profile ────────────────────────────────────────────────────────
async function loadMe() {{
  document.getElementById('me-content').innerHTML = '<div class="loader"><div class="spinner"></div></div>';
  const uid = INITIAL_UID || getTelegramUid();
  if (!uid) {{
    document.getElementById('me-content').innerHTML = renderEmpty('😕', 'Профиль не найден', 'Нажми /start в Telegram');
    return;
  }}
  try {{
    const r = await fetch('/api/profile/' + uid);
    if (!r.ok) throw new Error();
    myProfile = await r.json();
    document.getElementById('me-content').innerHTML = renderProfile(myProfile);
  }} catch {{
    document.getElementById('me-content').innerHTML = renderEmpty('😕', 'Профиль не найден', 'Нажми /start боту в Telegram');
  }}
}}

function getTelegramUid() {{
  try {{
    const tg = window.Telegram?.WebApp;
    return tg?.initDataUnsafe?.user?.id || 0;
  }} catch {{ return 0; }}
}}

// ── Render profile ─────────────────────────────────────────────────────────
function renderProfile(p) {{
  const avatarHtml = p.avatar_url
    ? `<img class="avatar" src="${{p.avatar_url}}" alt="">`
    : `<div class="avatar-placeholder">${{p.full_name[0] || '?'}}</div>`;

  const premiumBadge = p.premium ? '<div class="premium-badge">⭐</div>' : '';

  const giftsHtml = p.gifts && p.gifts.length > 0
    ? p.gifts.map((g, i) => `
      <div class="gift-card" style="animation-delay:${{i*60}}ms">
        <div class="gift-header">
          <span style="font-size:20px">🎀</span>
          <span class="gift-name">${{g.id}}</span>
          <span class="gift-stars">${{g.stars}} ⭐</span>
        </div>
        <div class="gift-body">
          ${{g.text !== '—' ? `<div class="gift-row"><span class="gr-label">Подпись</span><span class="gr-val">${{g.text}}</span></div>` : ''}}
          <div class="gift-row"><span class="gr-label">От</span><span class="gr-val">${{g.sender}} <span style="font-size:11px;opacity:.6">#${{g.sender_id}}</span></span></div>
          <div class="gift-row"><span class="gr-label">Дата</span><span class="gr-val">${{g.date}}</span></div>
          ${{g.private ? '<div class="gift-row"><span class="gr-label">Статус</span><span class="gift-private">🔒 Приватный</span></div>' : ''}}
        </div>
      </div>`).join('')
    : `<div style="margin:0 16px 16px"><div class="empty"><div class="empty-icon">🎁</div><div class="empty-text">Нет подарков</div><div class="empty-sub">или они скрыты</div></div></div>`;

  const badges = [
    ['Premium',    p.premium,    '⭐ Да', '❌ Нет'],
    ['Верифицирован', p.verified, '✅ Да', '❌ Нет'],
    ['Ограничен',  p.restricted, '⚠️ Да', '✅ Нет'],
    ['Scam / Fake', p.scam || p.fake, '🚨 Да', '✅ Нет'],
    ['Язык',       null, p.language, p.language],
    ['Регистрация', null, p.reg_date, p.reg_date],
  ];

  const cardsHtml = badges.map(([label, flag, yes, no], i) => {{
    const val = flag === null ? yes : (flag ? yes : no);
    const cls = flag === null ? '' : (flag ? (label === 'Premium' || label === 'Верифицирован' ? 'yes' : 'warn') : (label === 'Ограничен' || label.includes('Scam') ? 'yes' : 'no'));
    return `<div class="info-card" style="animation-delay:${{(i+1)*60}}ms">
      <div class="label">${{label}}</div>
      <div class="value ${{cls}}">${{val}}</div>
    </div>`;
  }}).join('');

  return `
    <div class="profile-hero">
      <div class="avatar-wrap">
        ${{avatarHtml}}
        ${{premiumBadge}}
      </div>
      <div class="hero-info">
        <div class="hero-name">${{p.full_name}}</div>
        ${{p.username ? `<div class="hero-username">@${{p.username}}</div>` : ''}}
        <div class="hero-id">ID: ${{p.id}}</div>
      </div>
    </div>
    <div class="cards-grid">${{cardsHtml}}</div>
    <div class="section-title">Подарки (${{p.gifts?.length || 0}})</div>
    ${{giftsHtml}}
  `;
}}

// ── People / Search ────────────────────────────────────────────────────────
async function loadPeople() {{
  if (allProfiles.length > 0) {{ renderPeople(allProfiles); return; }}
  document.getElementById('search-results').innerHTML = '<div class="loader"><div class="spinner"></div></div>';
  try {{
    const r = await fetch('/api/profiles');
    allProfiles = await r.json();
    renderPeople(allProfiles);
  }} catch {{
    document.getElementById('search-results').innerHTML = renderEmpty('😕', 'Ошибка загрузки', '');
  }}
}}

function filterPeople() {{
  const q = document.getElementById('search-input').value.toLowerCase().trim();
  const filtered = q
    ? allProfiles.filter(p =>
        p.full_name.toLowerCase().includes(q) ||
        (p.username || '').toLowerCase().includes(q) ||
        String(p.id).includes(q)
      )
    : allProfiles;
  renderPeople(filtered);
}}

function renderPeople(list) {{
  const el = document.getElementById('search-results');
  if (!list.length) {{
    el.innerHTML = renderEmpty('🔍', 'Никого не найдено', 'Попробуй другой запрос');
    return;
  }}
  el.innerHTML = list.map((p, i) => {{
    const avatarHtml = p.avatar_url
      ? `<img class="person-avatar" src="${{p.avatar_url}}" style="object-fit:cover" alt="">`
      : `<div class="person-avatar">${{p.full_name[0] || '?'}}</div>`;
    const giftCount = p.gifts?.length || 0;
    return `<div class="person-card" style="animation-delay:${{i*40}}ms" onclick="openPerson(${{p.id}})">
      ${{avatarHtml}}
      <div class="person-info">
        <div class="person-name">${{p.full_name}}</div>
        <div class="person-sub">${{p.username ? '@' + p.username + ' · ' : ''}}ID: ${{p.id}}</div>
      </div>
      ${{giftCount > 0 ? `<span class="person-chip">🎁 ${{giftCount}}</span>` : ''}}
    </div>`;
  }}).join('');
}}

function openPerson(uid) {{
  // Переключаемся на вкладку профиля и загружаем чужой профиль
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  document.getElementById('screen-me').classList.add('active');
  document.querySelector('.nav-item').classList.add('active');
  document.querySelector('.top-bar h1').textContent = 'Профиль';
  
  document.getElementById('me-content').innerHTML = '<div class="loader"><div class="spinner"></div></div>';
  fetch('/api/profile/' + uid)
    .then(r => r.json())
    .then(p => {{ document.getElementById('me-content').innerHTML = renderProfile(p); }})
    .catch(() => {{ document.getElementById('me-content').innerHTML = renderEmpty('😕', 'Не найдено', ''); }});
}}

function renderEmpty(icon, text, sub) {{
  return `<div class="empty"><div class="empty-icon">${{icon}}</div><div class="empty-text">${{text}}</div><div class="empty-sub">${{sub}}</div></div>`;
}}

// ── Init ───────────────────────────────────────────────────────────────────
window.Telegram?.WebApp?.ready();
window.Telegram?.WebApp?.expand();
loadMe();
</script>
</body>
</html>"""
