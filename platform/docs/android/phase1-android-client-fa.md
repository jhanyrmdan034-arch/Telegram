# فاز ۱ — زیرساخت ارتباطی اندروید با بک‌اند اختصاصی

تاریخ: ۲۰۲۶-۱۱-۰۴ · شاخه: `arena/76249c66-telegram` · وضعیت: **کد نوشته و اعتبارسنجی شده؛ کامپایل APK در این محیط ممکن نیست** (توضیح در بخش ۹).

---

## ۱. هدف این فاز

ساخت یک لایهٔ ارتباطی مستقل و ماژولار که اپلیکیشن اندروید موجود را به بک‌اند اختصاصی وصل کند، **بدون هیچ تغییری در رابط کاربری**. لایهٔ جدید:

- هیچ ارجاعی به `ConnectionsManager`، `SendMessagesHelper` و `MTProto` ندارد (به‌صورت خودکار بررسی می‌شود)،
- تمام عملیات UI را به درخواست‌های REST/WebSocket بک‌اند اختصاصی تبدیل می‌کند،
- می‌تواند در کنار مسیر قدیمی (MTProto) فعال بماند تا مهاجرت صفحه‌به‌صفحه انجام شود.

## ۲. ساختار پکیج

مسیر: `TMessagesProj/src/main/java/org/telegram/platform/`

| فایل | مسئولیت |
|---|---|
| `PlatformConfig.java` | آدرس REST/WS، تایم‌اوت‌ها، سقف آپلود |
| `PlatformException.java` | نوع‌بندی خطا: `OFFLINE`, `NETWORK`, `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `VALIDATION`, `RATE_LIMITED`, `SERVER` |
| `net/HttpTransport.java` | انتزاع HTTP (برای تست و جایگزینی) |
| `net/UrlConnectionTransport.java` | پیاده‌سازی با `HttpURLConnection` (API 23+) |
| `net/PlatformApi.java` | کلاینت REST: هدر Bearer، JSON با Gson موجود در اپ، **refresh تک‌پروازی (single-flight)** روی 401، تبدیل خطا |
| `auth/TokenStore.java` | اینترفیس ذخیرهٔ توکن + پیاده‌سازی حافظه‌ای |
| `auth/KeystoreTokenStore.java` | ذخیرهٔ رمزنگاری‌شده با AES-256-GCM و کلید Android Keystore |
| `auth/AuthManager.java` | ثبت‌نام، ورود با رمز، درخواست/تأیید OTP، بازیابی نشست، لیست دستگاه‌ها، ابطال نشست، خروج |
| `realtime/SocketTransport.java` | انتزاع WebSocket |
| `realtime/NvWebSocketTransport.java` | پیاده‌سازی با `nv-websocket-client` (از قبل در پروژه هست، وابستگی جدیدی اضافه نشد) |
| `realtime/RealtimeClient.java` | اتصال، reconnect نمایی با jitter، ping دوره‌ای، توزیع رویداد، typing/read |
| `store/LocalStore.java` | اینترفیس کش آفلاین + صندوق خروجی (outbox) |
| `store/SqliteLocalStore.java` | پیاده‌سازی SQLite (چت‌ها، پیام‌ها، outbox، id-map) |
| `store/MemoryLocalStore.java` | پیاده‌سازی تست |
| `repo/ChatRepository.java` | لیست دیالوگ، سنجاق، پوشه‌ها، باز کردن گفتگوی خصوصی، جست‌وجوی کاربر |
| `repo/MessageRepository.java` | تاریخچه صفحه‌بندی‌شده، ارسال خوش‌بینانه، ویرایش، حذف، فوروارد، جست‌وجوی درون‌گفتگو، ارسال مجدد خودکار |
| `mapper/IdMapper.java` | تبدیل پایدار UUID ↔ شناسهٔ عددی (`Chat.id` از نوع long، `Message.id` از نوع int) با تشخیص تداخل |
| `mapper/DtoMapper.java` | تبدیل JSON بک‌اند به `TLRPC.TL_chat/TL_user/TL_message` (تنها نقطه‌ای که `org.telegram.tgnet` را می‌بیند) |
| `PlatformBridge.java` | نقطهٔ ورود یکتا: سیم‌کشی همهٔ اجزا + `startSession()/endSession()` |

اندازه: ۲۰ فایل، ۲٬۴۹۲ خط، ۲۸ کلاس/اینترفیس، ۲۰۱ متد.

## ۳. جریان احراز هویت (فاز ۲ آماده است)

```
LoginActivity ──► PlatformBridge.auth()
                    ├─ requestOtp(username, "login")  → POST /api/v1/auth/otp/request
                    │      پاسخ: {delivery, expires_in, debug_code?}
                    ├─ verifyOtp(username, code)      → POST /api/v1/auth/otp/verify
                    │      پاسخ: {access_token, refresh_token}
                    ├─ tokens.save(...)  (رمزنگاری‌شده در Keystore)
                    ├─ GET /users/me → currentUser
                    └─ realtime.connect()
```

- **تمدید خودکار توکن:** هر درخواست ۴۰۱ بگیرد، `PlatformApi` یک‌بار `POST /auth/refresh` می‌زند؛ قفل `refreshLock` تضمین می‌کند N درخواست هم‌زمان فقط **یک** refresh بسازند و بقیه از توکن تازه استفاده کنند. اگر refresh شکست بخورد، توکن‌ها پاک و `PlatformException(UNAUTHORIZED)` پرتاب می‌شود تا UI به صفحهٔ ورود برگردد.
- **خروج:** `POST /auth/logout` سپس پاک‌سازی محلی (حتی اگر شبکه قطع باشد).
- **مدیریت دستگاه:** `GET /auth/sessions` و `DELETE /auth/sessions/{id}`.

## ۴. جریان دادهٔ صفحهٔ اصلی (فاز ۳ آماده است)

```
DialogsActivity
   ├─ chats().cachedChats()         ← SQLite (رسم فوری، حتی آفلاین)
   ├─ chats().refreshChats(cb)      ← GET /api/v1/chats
   │     هر ردیف شامل: last_message، unread_count، pinned، peer{display_name,is_online،last_seen_at}
   ├─ chats().pinnedChats(cb)       ← GET /api/v1/chats/pinned
   ├─ chats().folders(cb)           ← GET /api/v1/folders  |  PUT /folders/{id}/chats
   └─ realtime().connect()          ← WS /api/v1/ws?token=...
         رویدادها: ready، message.new، message.edited، message.deleted، typing، read، ...
```

- **آنلاین بودن:** بک‌اند وضعیت هر کاربر را از رجیستری اتصال‌های WebSocket می‌خواند (`runtime.manager.online_user_ids()`) و در `peer.is_online` می‌گذارد؛ بنابراین در تست واقعی، با باز شدن سوکت کاربر مقابل، مقدار `true` شد.
- **سنجاق:** `POST /chats/{id}/pin` و مرتب‌سازی سنجاق‌شده‌ها در ابتدای لیست سمت سرور انجام می‌شود (کلاینت هم کش را به همان ترتیب ذخیره می‌کند).

## ۵. جریان پیام (فاز ۴ آماده است)

```
ChatActivity
   ├─ messages().cachedHistory(chatId, 50)     ← SQLite
   ├─ messages().loadHistory(chatId, before, 50) ← GET /chats/{id}/messages?limit=&before_seq=
   │       پاسخ: {items:[...], next_before_seq}
   ├─ messages().send(chatId, text, replyToId, cb)
   │       POST /chats/{id}/messages {type,text,client_message_id,reply_to}
   │       ├─ موفق → ذخیره در کش، حباب «ارسال شد»
   │       └─ خطای قابل‌تلاش دوباره → outbox + «در صف»؛ flush خودکار روی reconnect
   ├─ messages().edit/delete/forward/search
   └─ realtime: message.new → applyRealtimeEvent() → به‌روزرسانی کش
```

- **`client_message_id`** در `meta` پاسخ برگردانده می‌شود؛ کلاینت با آن پیام محلی را با پیام سروری تطبیق می‌دهد (تست‌شده).
- **ارسال مجدد بعد از قطعی:** `flushOutbox()` هم روی رویداد «اتصال برقرار شد» و هم با تایمر ۲۰ ثانیه‌ای اجرا می‌شود؛ خطای دائمی (۴xx غیرقابل‌تلاش) از صف حذف و به‌عنوان «ناموفق» نمایش داده می‌شود.
- **تاریخچه پس از خروج/ورود:** کش محلی با `endSession()` پاک می‌شود (تا دادهٔ کاربر قبلی نماند) و پس از ورود مجدد از سرور بازسازی می‌شود — یعنی تاریخچه از دیتابیس خودمان می‌آید، نه از حافظهٔ کلاینت.

## ۶. نگاشت مدل‌ها (بدون بازنویسی UI)

UI به شناسه‌های عددی وابسته است (`TLRPC.Chat.id` از نوع `long`، `TLRPC.Message.id` از نوع `int`) در حالی که بک‌اند UUID می‌دهد:

- `IdMapper` برای هر UUID یک شناسهٔ عددی **پایدار** می‌سازد (SHA-256 + ماسک بیتی) و نگاشت معکوس را در SQLite نگه می‌دارد؛ در صورت تداخل هش، با salt بعدی جست‌وجو می‌کند تا هرگز دو موجودیت یکی نشوند.
- `DtoMapper` فیلدهای واقعی TLRPC را از JSON پر می‌کند: `title/participants_count/megagroup/broadcast/username/date` برای چت، `first_name/last_name/username/status` برای کاربر، `id/dialog_id/peer_id/from_id/message/date/out/edit_date/fwd_from` برای پیام.
- قاعدهٔ `dialog_id`: گفتگوی خصوصی = شناسهٔ طرف مقابل، گروه/کانال = منفیِ شناسهٔ چت.

## ۷. سیم‌کشی در اپ موجود (بدون تغییر ظاهر)

| نقطهٔ اتصال | فایل و خط | کار |
|---|---|---|
| راه‌اندازی | `messenger/ApplicationLoader.java:283` (`onCreate`) | `PlatformBridge.init(this, PlatformConfig.builder()...build())` سپس اگر توکن هست `startSession()` |
| ورود | `ui/LoginActivity.java:223` | جایگزینی فراخوانی‌های `ConnectionsManager.sendRequest` در مسیر ورود با `auth().requestOtp/verifyOtp` (ظاهر ویوها دست‌نخورده) |
| لیست دیالوگ | `ui/DialogsActivity.java:2988` (`loadDialogs`) | منبع داده: `chats().cachedChats()` + `refreshChats()` |
| گفتگو | `ui/ChatActivity.java:372` | تاریخچه از `messages().loadHistory()`، ارسال از `messages().send()` |
| ارسال | `messenger/SendMessagesHelper.java` | شاخهٔ جدید: اگر `PlatformBridge.isReady()` باشد، `messages().send()` به‌جای `sendRequest` |

نکتهٔ مهم: تا وقتی این چهار نقطه سیم‌کشی نشده‌اند، هیچ رفتاری از اپ تغییر نمی‌کند؛ پکیج جدید کاملاً غیرفعال است. این همان طراحی «همزیستی» است که اجازه می‌دهد مرحله‌به‌مرحله و بدون شکستن UI مهاجرت کنیم.

## ۸. تغییرات بک‌اند که این فاز لازم داشت

| افزوده | مسیر | تست |
|---|---|---|
| ورود با OTP | `POST /api/v1/auth/otp/request`، `POST /api/v1/auth/otp/verify` | ۶ تست (پذیرش، تک‌مصرف، باطل‌شدن کد قبلی، محدودیت تلاش، کاربر ناشناس، حساب مسدود) |
| پیکربندی عمومی | `GET /api/v1/config` (بدون احراز هویت) | ۱ تست (+ استثنا شدن از حالت تعمیرات) |
| غنی‌سازی لیست دیالوگ | `last_message`، `unread_count`، `pinned`، `peer` در `ChatOut` | ۲ تست |
| سنجاق | `GET /chats/pinned`، `POST /chats/{id}/pin` | ۱ تست |
| پوشه‌های چت | `routers/folders.py` (CRUD + `/chats`) | ۱ تست |
| فوروارد | `POST /messages/{id}/forward` | ۲ تست |
| جست‌وجوی درون‌گفتگو | `GET /chats/{id}/messages/search` | ۱ تست |
| مهاجرت پایگاه‌داده | `alembic/versions/32aa034598df_otp_chat_folders.py` | `alembic check` بدون اختلاف، downgrade/upgrade سالم |

پیام‌رسانی SMS: `app/notifications.py` با `SmsSender` (پیش‌فرض `LogSmsSender`؛ اتصال به ارائه‌دهندهٔ واقعی فقط با `SMS_PROVIDER` و کلید محیطی).

## ۹. اعتبارسنجی انجام‌شده و محدودیت صریح

**انجام‌شده (واقعی):**

1. `pytest` بک‌اند: **۹۳ تست موفق** (۷۸ تست قبلی بدون تغییر + ۱۵ تست جدید) — هیچ تستی حذف یا تضعیف نشد.
2. `alembic check` روی PostgreSQL واقعی: بدون drift؛ چرخهٔ downgrade/upgrade سالم.
3. `tools/check_android_phase1.py`: هر ۲۰ فایل با `javalang` پارس شد؛ صفر ارجاع به MTProto؛ هر ۲۱ مسیر REST استفاده‌شده در `openapi.json` وجود دارد.
4. `tools/android_contract_probe.py` روی سرور زندهٔ واقعی: **۵۹ بررسی، همه موفق** — از `GET /config` و ثبت‌نام و OTP و refresh تا ساخت گفتگو، فیلدهای لیست دیالوگ، **دریافت واقعی `message.new` روی WebSocket**، شمارندهٔ خوانده‌نشده، سنجاق، پوشه، فوروارد، جست‌وجو و ابطال نشست با خروج.

**انجام‌نشده و دلیل آن (بدون ادعای بی‌پایه):**

- **کامپایل کد جاوا انجام نشد**، چون در این محیط نه JDK هست نه Android SDK و هیچ‌کدام قابل دریافت نبودند (`javac` موجود نیست؛ نصب `install-jdk`/`jdk4py` فقط JRE بدون کامپایلر داد). بنابراین اعتبارسنجی کد جاوا «سینتکس + قرارداد API» است، نه اجرای بایت‌کد.
- **هیچ صفحه‌ای از UI هنوز به این لایه سیم‌کشی نشده است**؛ طبق ترتیب فازها، سیم‌کشی ورود در فاز ۲ و لیست/گفتگو در فازهای ۳ و ۴ انجام می‌شود. تا آن زمان اپ رفتار قبلی خود را دارد (هیچ قابلیتی حذف نشده).

## ۱۰. گام بعدی (فاز ۲)

1. سیم‌کشی `LoginActivity` به `AuthManager` (فقط منبع داده عوض می‌شود؛ ویوها و انیمیشن‌ها دست‌نخورده).
2. پاک‌سازی نشست‌های منقضی در استارتاپ + صفحهٔ «دستگاه‌های فعال».
3. سناریوی تست: ورود واقعی روی سرور زنده، خروج، ورود دوباره، بررسی حفظ تاریخچه از دیتابیس.

## ۱۱. قابلیت‌های بدون بک‌اند (یادآوری از فاز ۰)

بر اساس `dependency-map.json`، ۱۹ صفحه به billing (استارز/پریمیوم/کیف پول) و ۳ صفحه به bots وابسته‌اند و در بک‌اند اختصاصی معادلی ندارند. طبق قواعد مأموریت این‌ها **حذف نمی‌شوند**؛ فقط در فازهای بعد به‌صورت مستند غیرفعال/مخفی می‌شوند تا ظاهر و ساختار اپ دست‌نخورده بماند. فهرست کامل در `phase0-inventory.md` ستون «missing backend» است.
