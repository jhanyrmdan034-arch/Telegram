# معماری پلتفرم پیام‌رسان مستقل

> این سند وضعیت **پیاده‌سازی‌شده** را توصیف می‌کند، نه یک طرح روی کاغذ.
> هر بخشی که هنوز کامل نیست، در `STATUS-fa.md` صریحاً به‌عنوان «ناتمام» علامت‌گذاری شده است.

---

## ۱. نمای کلی

```
                      ┌──────────────────────────────────────────┐
   اپ اندروید  ──────▶│  Nginx  (TLS, rate limit, WS upgrade)    │
   (رابط فعلی)        └───────────────┬──────────────────────────┘
                                      │
                      ┌───────────────▼──────────────────────────┐
   پنل وب  ──────────▶│  FastAPI  (REST + WebSocket + /admin SPA) │
                      └───┬───────────┬──────────────┬───────────┘
                          │           │              │
                   ┌──────▼───┐ ┌─────▼────┐  ┌──────▼─────────┐
                   │PostgreSQL│ │  Redis   │  │  Object/Local  │
                   │  16      │ │ cache+   │  │  storage       │
                   │ (منبع    │ │ pub/sub  │  │  (files)       │
                   │  حقیقت)  │ │ +ratelimit│ └────────────────┘
                   └──────────┘ └──────────┘
                                      │
                             ┌────────▼────────┐
                             │  TURN / SFU     │  ← رسانهٔ تماس
                             │  (coturn)       │
                             └─────────────────┘
```

اصل طراحی: **یک منبع حقیقت** (PostgreSQL)، **حالت گذرا در Redis** (cache، شمارندهٔ rate limit، fan-out رویداد بین نمونه‌ها)، و **بدون هیچ وابستگی به سرورهای تلگرام**.

---

## ۲. مؤلفه‌ها

### ۲.۱ Backend (`platform/backend`)

| مؤلفه | فایل | توضیح |
|---|---|---|
| اپلیکیشن | `app/main.py` | factory، lifespan، middleware محافظ (maintenance + rate limit)، هندلر خطا که خطاها را در `error_log` ثبت می‌کند |
| پیکربندی | `app/config.py` | فقط از Environment Variable؛ در محیط production با مقادیر ناامن بالا نمی‌آید (`validate_for_production`) |
| مدل داده | `app/models.py` | ۲۰ جدول، کلیدهای UUID، JSONB برای تنظیمات/متادیتا |
| مهاجرت | `alembic/` | یک revision اولیهٔ کامل، **برگشت‌پذیر** و بدون drift نسبت به مدل‌ها |
| امنیت | `app/security.py` | Argon2id برای رمز، JWT کوتاه‌عمر، refresh token تصادفی ۳۸۴ بیتی که فقط **هش SHA-256** آن ذخیره می‌شود |
| دسترسی | `app/deps.py`, `app/permissions.py` | RBAC با ۱۷ مجوز و ۶ نقش سیستمی + نقش سفارشی |
| رویداد بلادرنگ | `app/realtime.py` | رجیستری WebSocket + گذرگاه رویداد (درون‌حافظه‌ای یا Redis pub/sub برای چند نمونه) |
| محدودیت نرخ | `app/ratelimit.py` | پنجرهٔ ثابت با Redis، fallback درون‌حافظه‌ای برای تک‌نمونه |
| ذخیره‌سازی فایل | `app/storage.py` | content-addressed (sha256)، محدودیت حجم، جلوگیری از path traversal |
| روترها | `app/routers/*.py` | ۸۲ عملیات REST + یک WebSocket |

### ۲.۲ پنل مدیریت (`platform/admin`)

SPA بدون هیچ وابستگی خارجی (بدون npm، بدون CDN) که توسط همان پروسهٔ API در `/admin` سرو می‌شود:

- ورود با حساب کارمند، ذخیرهٔ توکن در `sessionStorage`، تازه‌سازی خودکار روی 401
- صفحات: Dashboard، Users، Groups & Channels، Reports، Support، Staff & Roles، Settings، Audit & Security، Monitoring
- نمودارها SVG دست‌ساز از دادهٔ واقعی `/admin/stats/timeseries`
- خروجی CSV/PDF با هدر Authorization
- نمایش/پنهان‌سازی منوها و دکمه‌ها بر اساس مجوزهای همان کاربر

### ۲.۳ زیرساخت (`platform/deploy`)

`docker-compose` با ۶ سرویس: PostgreSQL، Redis، API، Nginx، coturn، و سرویس backup. به‌همراه اسکریپت‌های `backup.sh` (dump فشرده + آرشیو فایل‌ها + رمزنگاری اختیاری + retention)، `restore.sh`، `deploy.sh` (build → migrate → restart → healthcheck) و `Makefile`.

---

## ۳. مدل داده (۲۰ جدول)

| حوزه | جداول |
|---|---|
| هویت | `users`, `devices`, `sessions` |
| شبکهٔ اجتماعی | `contacts`, `blocked_users` |
| گفتگو | `chats`, `chat_members`, `messages` |
| رسانه | `files`, `sticker_sets`, `stickers` |
| تماس | `calls` |
| حاکمیت | `roles`, `reports`, `tickets`, `ticket_messages`, `audit_log`, `security_events`, `error_log`, `app_settings` |

نکات مهم طراحی:

- **ترتیب پیام‌ها بدون قفل برنامه‌ای:** شمارهٔ ترتیبی هر گفتگو با یک `UPDATE chats SET last_seq = last_seq + 1 ... RETURNING last_seq` گرفته می‌شود؛ هم‌زمانی در سطح دیتابیس حل می‌شود و `UNIQUE(chat_id, seq)` از تکرار جلوگیری می‌کند. تست بار این را تأیید می‌کند (۲۰۰ پیام هم‌زمان → `seq` دقیقاً ۱..۲۰۰ بدون خطا).
- **چت خصوصی تک‌تا:** ستون `private_key = "min_id:max_id"` با index یکتا مانع ساخت تکراری می‌شود.
- **حذف نرم پیام:** `deleted_at` + پاک‌سازی متن؛ تاریخچه همچنان سازگار می‌ماند.
- **حسابرسی:** هر عملیات مدیریتی در `audit_log` با actor، IP و متادیتا ثبت می‌شود؛ رویدادهای امنیتی جدا در `security_events`.

---

## ۴. سطح API

۸۲ عملیات، ۴۵ schema، مستندات زنده در `/docs` و `/redoc` و نسخهٔ ثابت در `docs/openapi.json`.

| گروه | تعداد | نمونه |
|---|---|---|
| `auth` | ۷ | register, login, refresh, logout, sessions |
| `users` | ۷ | me, search, block/unblock, profile |
| `contacts` | ۳ | list, add, remove |
| `chats` | ۱۲ | private/group/channel/saved، members، read، typing، leave |
| `messages` | ۵ | send، history با cursor، edit، delete |
| `files` | ۴ | upload، metadata، download، delete |
| `calls` | ۴ | start، status، history، ICE config |
| `support` | ۷ | reports، tickets، ticket thread |
| `admin` | ۳۱ | dashboard، moderation، roles، settings، audit، exports |
| `health` | ۲ | livez/readyz |

### WebSocket (`/api/v1/ws?token=…`)

رویدادهای سرور→کلاینت: `ready`, `message.new`, `message.edited`, `message.deleted`, `message.read`, `typing`, `chat.created`, `chat.members_added`, `chat.member_removed`, `call.incoming`, `call.updated`, `ticket.message`.
کلاینت→سرور: `ping`, `typing`, `read`.

---

## ۵. امنیت

| موضوع | پیاده‌سازی |
|---|---|
| رمز عبور | Argon2id (۳ iteration, 64MB, 4 lane) |
| Access token | JWT HS256 با TTL پیش‌فرض ۹۰۰ ثانیه، شامل `sid` برای ابطال فوری |
| Refresh token | رشتهٔ تصادفی ۳۸۴ بیتی، ذخیرهٔ هش، **rotation** در هر استفاده |
| تشخیص سرقت توکن | استفادهٔ مجدد از توکن چرخیده‌شده → ابطال کل خانوادهٔ نشست + رویداد امنیتی |
| ابطال نشست | بلوکه‌شدن کاربر، خروج، یا حذف نشست بلافاصله روی access token اثر می‌گذارد (تست‌شده) |
| Rate limiting | پنجرهٔ ثابت Redis (پیش‌فرض ۳۰۰/دقیقه per IP) + سقف سخت‌گیرانه‌تر روی login/register؛ لایهٔ دوم در Nginx |
| SQL Injection | تمام پرس‌وجوها پارامتری (SQLAlchemy Core/ORM)؛ تست با payload واقعی |
| Path traversal | اعتبارسنجی کلید ذخیره‌سازی؛ تست‌شده |
| دسترسی به فایل | مالک، عمومی، یا عضویت در چتی که فایل در آن ارسال شده |
| RBAC | مجوزهای ریزدانه؛ نقش `super_admin` غیرقابل‌تغییر؛ ارتقای نقش فقط توسط super admin |
| حسابرسی | هر عمل مدیریتی + رویدادهای امنیتی + لاگ خطای سرور در دیتابیس |
| TLS | در Nginx با TLS1.2/1.3، HSTS، و محدودیت متد/نرخ |
| رازها | فقط از Environment؛ در production با مقدار پیش‌فرض بالا نمی‌آید |

---

## ۶. محدودیت‌های شناخته‌شدهٔ فعلی

1. **رمزنگاری سرتاسری (E2EE)** پیاده‌سازی نشده است. پیام‌ها در دیتابیس به‌صورت متن ذخیره می‌شوند (در محدودهٔ TLS). طراحی پیشنهادی و فازبندی در `STATUS-fa.md`.
2. **رسانهٔ تماس** در این سرویس نیست؛ فقط سیگنالینگ (ایجاد/وضعیت/ICE) پیاده شده و رله به coturn سپرده شده است.
3. **ذخیره‌سازی فایل** نسخهٔ local است؛ اینترفیس `Storage` برای S3/MinIO آماده است ولی پیاده‌سازی S3 نوشته نشده.
4. **نقش «آنلاین»** در استقرار چند-نمونه‌ای باید از Redis خوانده شود؛ فعلاً per-process است.
5. **Stickers**: اسکیمای دیتابیس و API خواندن وجود دارد، اما آپلود/مدیریت بستهٔ استیکر و رندر TGS سمت کلاینت تکمیل نشده.
6. **اپ اندروید** هنوز به این API متصل نیست — جزئیات و نقشهٔ راه در `ANDROID-INTEGRATION-fa.md`.
