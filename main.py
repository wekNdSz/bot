import asyncio
import logging
import os
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

# ─── КОНФИГУРАЦИЯ ───────────────────────────────────────────────────────────
TOKEN        = os.getenv("BOT_TOKEN", "8895842204:AAGg3NVgJkCg7I6OVD_-Nkr7DfD4VxZw1jQ")
WEBHOOK_HOST = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL  = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}" if WEBHOOK_HOST else ""
WEB_URL      = f"https://{WEBHOOK_HOST}" if WEBHOOK_HOST else "http://localhost:8000"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()

# Внутреннее хранилище профилей (в продакшене лучше использовать БД)
profiles: dict[int, dict] = {}

# ─── ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ──────────────────────────────────────────────────
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

# ─── ОБРАБОТЧИК AIOGRAM ──────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user    = message.from_user
    chat_id = user.id

    full_info = None
    try:
        full_info = await bot.get_chat(chat_id)
    except Exception as e:
        log.warning(f"getChat error: {e}")

    gifts_raw = None
    try:
        resp = await bot.get_user_gifts(user_id=chat_id)
        gifts_raw = resp.gifts if resp and resp.gifts else None
    except Exception as e:
        log.warning(f"getUserGifts error: {e}")

    photos = None
    try:
        photos = await bot.get_user_profile_photos(chat_id, limit=1)
    except Exception as e:
        log.warning(f"getPhotos error: {e}")

    avatar_url = None
    if photos and photos.total_count > 0:
        try:
            file_id   = photos.photos[0][-1].file_id
            file_info = await bot.get_file(file_id)
            avatar_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        except Exception as e:
            log.warning(f"getFile error: {e}")

    name_parts = [user.first_name]
    if user.last_name:
        name_parts.append(user.last_name)

    # Сохраняем информацию о пользователе
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

    # Ссылки на Web App
    webapp_url = f"{WEB_URL}/app?uid={chat_id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🌐 Открыть профиль",
            web_app=WebAppInfo(url=webapp_url)
        )
    ]])

    # Сразу отправляем только одно красивое приветственное сообщение
    await message.answer(
        f"👋 Привет, <b>{profile['full_name']}</b>!\n\nТвой профиль успешно сгенерирован. Нажми на кнопку ниже, чтобы открыть его в стильном Web App.",
        reply_markup=kb
    )

# ─── FASTAPI LIFESPAN & WEBHOOKS ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)
        log.info(f"Webhook set to: {WEBHOOK_URL}")
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

# ─── API ENDPOINTS ───────────────────────────────────────────────────────────
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

# ─── FRONTEND (MATERIAL YOU) ─────────────────────────────────────────────────
def get_app_html(initial_uid: int) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<title>InfoAboutYou</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<style>
/* Material You Монохромная палитра с динамической адаптацией под тему */
:root {{
  --md-co-surface: #F9F9FC;
  --md-co-on-surface: #1A1C1E;
  --md-co-surface-variant: #E0E2EC;
  --md-co-on-surface-variant: #43474E;
  --md-co-primary: #0061A4;
  --md-co-on-primary: #FFFFFF;
  --md-co-primary-container: #D1E4FF;
  --md-co-on-primary-container: #001D36;
  --md-co-secondary-container: #E1E2EC;
  --md-co-on-secondary-container: #161C24;
  --md-co-outline: #73777F;
  --md-co-outline-variant: #C4C6D0;
  --md-co-error: #BA1A1A;
  --md-co-success: #2E6C00;
  
  --font: 'Roboto', sans-serif;
  --duration: 0.25s;
  --easing: cubic-bezier(0.2, 0, 0, 1);
}}

@media(prefers-color-scheme: dark) {{
  :root {{
    --md-co-surface: #111318;
    --md-co-on-surface: #E2E2E6;
    --md-co-surface-variant: #43474E;
    --md-co-on-surface-variant: #C4C6D0;
    --md-co-primary: #9ECAFF;
    --md-co-on-primary: #003258;
    --md-co-primary-container: #00497D;
    --md-co-on-primary-container: #D1E4FF;
    --md-co-secondary-container: #33353A;
    --md-co-on-secondary-container: #E2E2E6;
    --md-co-outline: #8C9199;
    --md-co-outline-variant: #43474E;
    --md-co-error: #FFB4AB;
    --md-co-success: #76DE35;
  }}
}}

* {{ box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }}

body {{
  font-family: var(--font);
  background: var(--md-co-surface);
  color: var(--md-co-on-surface);
  min-height: 100dvh;
  overflow-x: hidden;
  padding-bottom: calc(80px + env(safe-area-inset-bottom));
}}

/* Фиксированный Top App Bar */
.top-bar {{
  position: sticky; top: 0; z-index: 50;
  background: var(--md-co-surface);
  padding: calc(16px + env(safe-area-inset-top)) 16px 12px;
  display: flex; align-items: center; gap: 16px;
  border-bottom: 1px solid transparent;
  transition: border-color var(--duration) var(--easing), background var(--duration);
}}
.top-bar.scrolled {{
  border-color: var(--md-co-outline-variant);
  background: var(--md-co-surface);
}}
.top-bar .back-btn {{
  background: transparent; border: none; font-size: 22px; cursor: pointer;
  color: var(--md-co-on-surface); display: none; align-items: center; justify-content: center;
  width: 40px; height: 40px; border-radius: 50%;
}}
.top-bar .back-btn:active {{ background: var(--md-co-secondary-container); }}
.top-bar h1 {{ font-size: 22px; font-weight: 400; flex: 1; }}
.top-bar .refresh-btn {{
  width: 40px; height: 40px; display: flex; align-items: center; justify-content: center;
  border-radius: 50%; cursor: pointer; font-size: 20px;
}}
.top-bar .refresh-btn:active {{ background: var(--md-co-secondary-container); }}

/* Контейнеры экранов */
.screen {{ display: none; animation: fadeIn var(--duration) var(--easing) both; }}
.screen.active {{ display: block; }}
@keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(8px); }} to {{ opacity: 1; transform: translateY(0); }} }}

/* Поиск M3 */
.search-container {{ padding: 8px 16px 16px; }}
.search-bar {{
  display: flex; align-items: center; gap: 12px;
  background: var(--md-co-secondary-container);
  border-radius: 28px; padding: 0 16px; height: 56px;
}}
.search-bar input {{
  flex: 1; border: none; background: transparent; font-family: var(--font);
  font-size: 16px; color: var(--md-co-on-secondary-container); outline: none;
}}
.search-bar input::placeholder {{ color: var(--md-co-on-surface-variant); }}
.search-bar .icon {{ color: var(--md-co-on-surface-variant); font-size: 20px; }}

/* Герой Профиля (Material Design Banner) */
.profile-hero {{
  margin: 8px 16px 16px;
  background: var(--md-co-primary-container);
  color: var(--md-co-on-primary-container);
  border-radius: 28px; padding: 24px;
  display: flex; align-items: center; gap: 20px;
}}
.avatar-container {{ position: relative; flex-shrink: 0; }}
.avatar {{
  width: 84px; height: 84px; border-radius: 50%; object-fit: cover;
  background: var(--md-co-surface-variant); display: block;
}}
.avatar-placeholder {{
  width: 84px; height: 84px; border-radius: 50%;
  background: var(--md-co-primary); color: var(--md-co-on-primary);
  display: flex; align-items: center; justify-content: center;
  font-size: 32px; font-weight: 500;
}}
.premium-dot {{
  position: absolute; bottom: 0; right: 0; background: #FFB800;
  width: 24px; height: 24px; border-radius: 50%; border: 3px solid var(--md-co-primary-container);
  display: flex; align-items: center; justify-content: center; font-size: 11px;
}}
.hero-details {{ flex: 1; min-width: 0; }}
.hero-title {{ font-size: 22px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.hero-subtitle {{ font-size: 14px; opacity: 0.8; margin-top: 2px; font-weight: 500; }}
.hero-id {{ font-size: 12px; opacity: 0.6; margin-top: 6px; font-family: monospace; }}

/* Сетка карточек */
.info-grid {{
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin: 0 16px 24px;
}}
.info-card {{
  background: var(--md-co-secondary-container);
  color: var(--md-co-on-secondary-container);
  border-radius: 16px; padding: 16px;
  display: flex; flex-direction: column; gap: 4px;
}}
.info-card .label {{ font-size: 12px; font-weight: 500; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.5px; }}
.info-card .value {{ font-size: 16px; font-weight: 700; }}
.value.success {{ color: var(--md-co-success); }}
.value.error {{ color: var(--md-co-error); }}

/* Заголовки секций */
.section-title {{
  font-size: 14px; font-weight: 500; text-transform: uppercase;
  letter-spacing: 1px; color: var(--md-co-on-surface-variant);
  margin: 0 24px 12px;
}}

/* Карточки Подарков */
.gift-card {{
  background: var(--md-co-surface);
  border: 1px solid var(--md-co-outline-variant);
  border-radius: 20px; margin: 0 16px 12px; overflow: hidden;
}}
.gift-header {{
  background: var(--md-co-secondary-container); padding: 12px 16px;
  display: flex; align-items: center; justify-content: space-between;
}}
.gift-title {{ font-weight: 700; font-size: 15px; }}
.gift-badge {{
  background: var(--md-co-primary); color: var(--md-co-on-primary);
  padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 700;
}}
.gift-body {{ padding: 16px; display: flex; flex-direction: column; gap: 8px; font-size: 14px; }}
.gift-row {{ display: flex; justify-content: space-between; gap: 8px; }}
.gift-row .prop {{ color: var(--md-co-on-surface-variant); }}
.gift-row .val {{ font-weight: 500; text-align: right; max-width: 70%; }}

/* Список людей */
.people-list {{ display: flex; flex-direction: column; gap: 8px; padding: 0 16px; }}
.person-item {{
  display: flex; align-items: center; gap: 16px; padding: 12px 16px;
  background: var(--md-co-surface); border: 1px solid var(--md-co-outline-variant);
  border-radius: 16px; cursor: pointer; transition: background var(--duration);
}}
.person-item:active {{ background: var(--md-co-secondary-container); }}
.person-avatar {{ width: 48px; height: 48px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }}
.person-avatar-placeholder {{
  width: 48px; height: 48px; border-radius: 50%; background: var(--md-co-primary);
  color: var(--md-co-on-primary); display: flex; align-items: center; justify-content: center;
  font-size: 18px; font-weight: 700; flex-shrink: 0;
}}
.person-info {{ flex: 1; min-width: 0; }}
.person-name {{ font-weight: 700; font-size: 16px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.person-sub {{ font-size: 13px; color: var(--md-co-on-surface-variant); margin-top: 2px; }}
.person-badge {{
  background: var(--md-co-primary-container); color: var(--md-co-on-primary-container);
  padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 500;
}}

/* Нижняя навигация M3 Navigation Bar */
.nav-bar {{
  position: fixed; bottom: 0; left: 0; right: 0; z-index: 100;
  background: var(--md-co-surface);
  border-top: 1px solid var(--md-co-outline-variant);
  display: flex; height: calc(64px + env(safe-area-inset-bottom));
  padding-bottom: env(safe-area-inset-bottom);
}}
.nav-item {{
  flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
  background: transparent; border: none; cursor: pointer; gap: 4px;
  color: var(--md-co-on-surface-variant); font-family: var(--font); font-size: 12px; font-weight: 500;
}}
.nav-pill {{
  width: 64px; height: 32px; border-radius: 16px; display: flex; align-items: center;
  justify-content: center; transition: background var(--duration) var(--easing);
  font-size: 20px; color: var(--md-co-on-surface-variant);
}}
.nav-item.active .nav-pill {{
  background: var(--md-co-primary-container);
  color: var(--md-co-on-primary-container);
}}
.nav-item.active {{ color: var(--md-co-on-surface); font-weight: 700; }}

/* Состояния */
.empty-state {{ text-align: center; padding: 48px 24px; color: var(--md-co-on-surface-variant); }}
.empty-icon {{ font-size: 48px; margin-bottom: 12px; }}
.loader {{ display: flex; justify-content: center; padding: 48px; }}
.spinner {{
  width: 36px; height: 36px; border: 4px solid var(--md-co-outline-variant);
  border-top-color: var(--md-co-primary); border-radius: 50%;
  animation: spin 0.8s linear infinite;
}}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
</style>
</head>
<body>

<div class="top-bar" id="main-top-bar">
  <button class="back-btn" id="back-button" onclick="goBack()">←</button>
  <h1 id="top-bar-title">Мой профиль</h1>
  <div class="refresh-btn" id="refresh-button" onclick="handleRefresh()">🔄</div>
</div>

<div class="screen active" id="screen-profile">
  <div id="profile-content">
    <div class="loader"><div class="spinner"></div></div>
  </div>
</div>

<div class="screen" id="screen-search">
  <div class="search-container">
    <div class="search-bar">
      <span class="icon">🔍</span>
      <input type="text" id="search-input" placeholder="Поиск по имени или @username..." oninput="onSearchInput()">
    </div>
  </div>
  <div id="search-results" class="people-list"></div>
</div>

<nav class="nav-bar">
  <button class="nav-item active" id="nav-btn-profile" onclick="switchTab('profile')">
    <div class="nav-pill">👤</div>
    <span>Профиль</span>
  </button>
  <button class="nav-item" id="nav-btn-search" onclick="switchTab('search')">
    <div class="nav-pill">🔍</div>
    <span>Поиск</span>
  </button>
</nav>

<script>
const INITIAL_UID = {initial_uid};
let currentTab = 'profile';
let allProfiles = [];
let profileHistory = []; // История просмотров для кнопки "Назад"

// Получение ID из Telegram WebApp
function getTelegramUid() {{
  try {{
    return window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 0;
  }} catch (e) {{ return 0; }}
}}

// Инициализация при запуске
window.addEventListener('DOMContentLoaded', () => {{
  window.Telegram?.WebApp?.ready();
  window.Telegram?.WebApp?.expand();
  
  // Добавляем обработку скролла для смены стиля TopBar
  window.addEventListener('scroll', () => {{
    const topBar = document.getElementById('main-top-bar');
    if (window.scrollY > 10) {{
      topBar.classList.add('scrolled');
    }} else {{
      topBar.classList.remove('scrolled');
    }}
  }});

  // Загружаем изначальный профиль
  const targetUid = INITIAL_UID || getTelegramUid();
  if (targetUid) {{
    loadProfile(targetUid, true);
  }} else {{
    showEmptyProfile();
  }}
}});

// Переключение табов нижнего меню
function switchTab(tabName) {{
  if (currentTab === tabName && tabName === 'profile' && profileHistory.length <= 1) return;
  
  currentTab = tabName;
  
  // Обновление кнопок меню
  document.querySelectorAll('.nav-item').forEach(btn => btn.classList.remove('active'));
  document.getElementById('nav-btn-' + tabName).classList.add('active');
  
  // Переключение контейнеров
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById('screen-' + tabName).classList.add('active');
  
  if (tabName === 'search') {{
    profileHistory = []; // Сбрасываем стек истории при переходе на поиск
    updateTopBar('Поиск', false, false);
    loadAllProfiles();
  }} else {{
    const myUid = INITIAL_UID || getTelegramUid();
    loadProfile(myUid, true);
  }}
}}

// Обновление состояния Top App Bar
function updateTopBar(title, showBack, showRefresh) {{
  document.getElementById('top-bar-title').textContent = title;
  document.getElementById('back-button').style.display = showBack ? 'flex' : 'none';
  document.getElementById('refresh-button').style.display = showRefresh ? 'flex' : 'none';
}}

// Загрузка конкретного профиля по API
async function loadProfile(uid, isRoot = false) {{
  const container = document.getElementById('profile-content');
  container.innerHTML = '<div class="loader"><div class="spinner"></div></div>';
  
  if (isRoot) {{
    profileHistory = [uid];
    updateTopBar('Мой профиль', false, true);
  }} else {{
    updateTopBar('Профиль', true, false);
  }}

  try {{
    const response = await fetch('/api/profile/' + uid);
    if (!response.ok) throw new Error();
    const data = await response.json();
    container.innerHTML = renderProfileHtml(data);
  }} catch (err) {{
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">😕</div>
        <div class="empty-text">Профиль не синхронизирован</div>
        <div class="empty-sub">Введите команду /start в боте Telegram.</div>
      </div>`;
  }}
}}

// Обновление текущего профиля
function handleRefresh() {{
  if (profileHistory.length > 0) {{
    loadProfile(profileHistory[profileHistory.length - 1], profileHistory.length === 1);
  }}
}}

// Кнопка назад
function goBack() {{
  if (profileHistory.length > 1) {{
    profileHistory.pop(); // Удаляем текущий
    const prevUid = profileHistory[profileHistory.length - 1];
    loadProfile(prevUid, profileHistory.length === 1);
  }} else {{
    switchTab('search');
  }}
}}

// Открытие чужого профиля из поиска
function openUserDetail(uid) {{
  profileHistory.push(uid);
  
  // Переключаем визуал на вкладку профиля
  currentTab = 'profile';
  document.querySelectorAll('.nav-item').forEach(btn => btn.classList.remove('active'));
  document.getElementById('nav-btn-profile').classList.add('active');
  
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById('screen-profile').classList.add('active');
  
  loadProfile(uid, false);
}}

// Рендеринг HTML карточки профиля
function renderProfileHtml(p) {{
  const avatar = p.avatar_url 
    ? `<img class="avatar" src="${{p.avatar_url}}" alt="Avatar">`
    : `<div class="avatar-placeholder">${{p.full_name[0] || '?'}}</div>`;
    
  const premium = p.premium ? '<div class="premium-dot">⭐</div>' : '';
  
  // Обработка флагов/значков безопасности
  let statusText = "Чистый";
  let statusClass = "success";
  if (p.scam || p.fake) {{ statusText = "🚨 Scam/Fake"; statusClass = "error"; }}
  else if (p.restricted) {{ statusText = "⚠️ Ограничен"; statusClass = "error"; }}

  // Подарки
  let giftsHtml = '';
  if (p.gifts && p.gifts.length > 0) {{
    giftsHtml = p.gifts.map(g => `
      <div class="gift-card">
        <div class="gift-header">
          <span class="gift-title">🎀 Код: ${{g.id}}</span>
          <span class="gift-badge">${{g.stars}} ⭐</span>
        </div>
        <div class="gift-body">
          ${{g.text !== '—' ? `<div class="gift-row"><span class="prop">Сообщение</span><span class="val">${{g.text}}</span></div>` : ''}}
          <div class="gift-row"><span class="prop">Отправитель</span><span class="val">${{g.sender}} (ID: ${{g.sender_id}})</span></div>
          <div class="gift-row"><span class="prop">Дата получения</span><span class="val">${{g.date}}</span></div>
          ${{g.private ? `<div class="gift-row"><span class="prop">Приватность</span><span class="val" style="color:var(--md-co-error)">🔒 Личный подарок</span></div>` : ''}}
        </div>
      </div>
    `).join('');
  }} else {{
    giftsHtml = `
      <div class="empty-state">
        <div class="empty-icon">🎁</div>
        <div class="empty-text">Нет активных подарков</div>
      </div>`;
  }}

  return `
    <div class="profile-hero">
      <div class="avatar-container">
        ${{avatar}}
        ${{premium}}
      </div>
      <div class="hero-details">
        <div class="hero-title">${{p.full_name}}</div>
        ${{p.username ? `<div class="hero-subtitle">@${{p.username}}</div>` : ''}}
        <div class="hero-id">ID: ${{p.id}}</div>
      </div>
    </div>

    <div class="info-grid">
      <div class="info-card">
        <span class="label">Премиум</span>
        <span class="value ${{p.premium ? 'success' : ''}}">${{p.premium ? 'Активен' : 'Нет'}}</span>
      </div>
      <div class="info-card">
        <span class="label">Статус защиты</span>
        <span class="value ${{statusClass}}">${{statusText}}</span>
      </div>
      <div class="info-card">
        <span class="label">Язык интерфейса</span>
        <span class="value">${{p.language.toUpperCase()}}</span>
      </div>
      <div class="info-card">
        <span class="label">Верификация</span>
        <span class="value ${{p.verified ? 'success' : ''}}">${{p.verified ? 'Да' : 'Нет'}}</span>
      </div>
    </div>

    <div class="section-title">Коллекция подарков (${{p.gifts?.length || 0}})</div>
    ${{giftsHtml}}
  `;
}}

// Загрузка всех пользователей (для поиска)
async function loadAllProfiles() {{
  const resContainer = document.getElementById('search-results');
  resContainer.innerHTML = '<div class="loader"><div class="spinner"></div></div>';
  try {{
    const response = await fetch('/api/profiles');
    allProfiles = await response.json();
    renderPeopleList(allProfiles);
  }} catch(e) {{
    resContainer.innerHTML = '<div class="empty-state"><div class="empty-text">Ошибка загрузки списка</div></div>';
  }}
}}

// Поиск / Фильтрация списка
function onSearchInput() {{
  const query = document.getElementById('search-input').value.toLowerCase().trim();
  if(!query) {{
    renderPeopleList(allProfiles);
    return;
  }}
  const filtered = allProfiles.filter(p => 
    p.full_name.toLowerCase().includes(query) || 
    (p.username || '').toLowerCase().includes(query) || 
    String(p.id).includes(query)
  );
  renderPeopleList(filtered);
}}

// Отображение списка пользователей
function renderPeopleList(list) {{
  const resContainer = document.getElementById('search-results');
  if(!list.length) {{
    resContainer.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">🔍</div>
        <div class="empty-text">Никого не найдено</div>
      </div>`;
    return;
  }}
  
  resContainer.innerHTML = list.map(p => {{
    const avatar = p.avatar_url 
      ? `<img class="person-avatar" src="${{p.avatar_url}}" alt="">`
      : `<div class="person-avatar-placeholder">${{p.full_name[0] || '?'}}</div>`;
    const giftsCount = p.gifts?.length || 0;
    
    return `
      <div class="person-item" onclick="openUserDetail(${{p.id}})">
        ${{avatar}}
        <div class="person-info">
          <div class="person-name">${{p.full_name}}</div>
          <div class="person-sub">${{p.username ? '@'+p.username : 'ID: ' + p.id}}</div>
        </div>
        ${{giftsCount > 0 ? `<div class="person-badge">🎁 ${{giftsCount}}</div>` : ''}}
      </div>
    `;
  }}).join('');
}}

function showEmptyProfile() {{
  document.getElementById('profile-content').innerHTML = `
    <div class="empty-state">
      <div class="empty-icon">👤</div>
      <div class="empty-text">Не удалось определить профиль</div>
    </div>`;
}}
</script>
</body>
</html>"""
