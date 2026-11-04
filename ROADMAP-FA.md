# مسنجر自分 با UI تلگرام — roadmap (فارسی)

> Fork Android di Telegram (`jhanyrmdan034-arch/Telegram`), base: v13.1.1 (commit `3de37c8`)
> Strategia: **UI دقیقاً همان اپ تلگرام** (دست‌نخورده) + **بک‌اند کاملاً自分، هاست‌شده در ایران، کارا در نت ملی**

---

## ۱. هدف

ساخت یک مسنجر برای自分 که:

- از نظر کاربر، **دقیقاً همان اپ تلگرام** باشد — همه دکمه‌ها، صفحه‌ها، انیمیشن‌ها و gestureها
- داده‌ها **فقط روی سرورهای自分 در ایران** باشند
- در شرایط **«نت ملی»** (قطع اینترنت بین‌الملل) کاملاً کار کند
- فازبندی: PoC → MVP → امکانات بیشتر

## ۲. واقعیت فنی کلیدی

- کد **سرور** تلگرام هرگز open source نشده است؛ فقط کلاینت‌ها و پروتکل MTProto公開.
  броне یعنی «اپ بدون هیچ تغییری + سرور自分» **ممکن نیست** — لایه شبکه کلاینت inevitable باید rewire شود.
- چیزی که 100٪ دست‌نخورده می‌ماند: **UI** (همه فایل‌های `org.telegram.ui`, layoutها، native UI).
- parity کامل با «همه امکانات تلگرام» (CDN جهانی، تماس، پرداخت، ربات‌ها...) کار **چندین سالِ تیم بزرگ** است؛
  پس scope نسخه اول باید تعریف شود.

## ۳. استراتژی: «UI تلگرام، موتور自分»

- **می‌ماند:** تمام UI، state management، média picker، gallery، voice recorder و...
- **عوض می‌شود:** لایه شبکه `org.telegram.tgnet` + native `jni/tgnet` → یک adapter جدید
  (WebSocket + protobuf/JSON روی TLS 1.3) که به API سرور自分 در ایران وصل می‌شود.
- **گزینه جایگزین برایSaving زمان بک‌اند:** به‌جای نوشتن بک‌اند از صفر، زیر капوت از
  **Signal Server** یا **Matrix/Synapse** (open source) استفاده شود و فقط کلاینت re-skin شود.

## ۴. карта изменений کد (مکان‌های پیدا شده در این ریپو)

| بخش | فایل | کار |
|---|---|---|
| آدرس دیتاسنترها | `TMessagesProj/jni/tgnet/ConnectionsManager.cpp` (~خط ۱۸۲۴) | IP/Domain سرورهای ایران |
| کلیدهای RSA سرور (handshake) | `TMessagesProj/jni/tgnet/Handshake.cpp`, `Datacenter.cpp` | کلید自分 یا حذف احراز هویت MTProto با protokol自分 |
| api_id / app_hash | `TMessagesProj/src/main/java/org/telegram/messenger/BuildVars.java` | api_id自分 |
| لایه شبکه | `org.telegram.tgnet.*` (~۲۲۰۰ فایل Java + native) | adapter به API自分 |
| push | `GcmPushListenerService.java`, `firebase-messaging` در `TMessagesProj/build.gradle`, `TMessagesProj/google-services.json` | push自分 از طریق WebSocket持久 + نوتیفیکیشن لوکال |
| stringهای cloud | `USE_CLOUD_STRINGS` در `BuildVars.java` (`translations.telegram.org`) | false + self-host |
| آپدیت | `CHECK_UPDATES` در `BuildVars.java` | false، آپدیت از سرور自分 |
| Google | `SAFETYNET_KEY`, `GOOGLE_AUTH_CLIENT_ID`, billing, play-services-* | حذف/غیرفعال (`IS_BILLING_UNAVAILABLE = true`) |
| لاگین | `LoginActivity` و flujo SMS/call | SMS از درگاه ایرانی (Kavenegar و...) + fallback username/password |
| Büro Distribution | — | APK مستقیم از سایت自分 + بازار/مایکت (Google Play در ایران نیست) |

## ۵. چک‌لیست «نت ملی» (همه endpointهای خارجی باید حذف شوند)

- [ ] FCM/Firebase push → push自分
- [ ] SafetyNet / Play Integrity / Google Auth
- [ ] Google Billing
- [ ] play-services-maps / vision / auth
- [ ] cloud strings (`translations.telegram.org`)
- [ ] update check (`CHECK_UPDATES`)
- [ ] دیتاسنترهای تلگرام (DC IPs)
- [ ] دامنه `.ir` + DNS داخلی + TLS
- [ ] SMS gateway ایرانی برای لاگین

## ۶. معماری سرور (ایران)

```
Client (forked Telegram Android, UI hand-nouched)
   │  WebSocket / HTTPS (TLS 1.3) — wss://chat.example.ir
   ▼
API Gateway (Nginx/Caddy + rate limit + DDoS protection)
   ├── Auth service      (SMS gateway ایرانی / username+password, JWT)
   ├── Chat service      (1:1, groups, messages) — PostgreSQL + Redis/RabbitMQ
   ├── Media service     (S3-compatible storage ایرانی)
   ├── Push自分           (اتصال持久 WebSocket + local notifications)
   └── Calls (فاز بعد)   WebRTC + TURN自分
Backups + monitoring + multi-location در ایران
```

## ۷. جدول زمانی واقع‌بینانه

| فاز | محتوا | زمان | تیم |
|---|---|---|---|
| PoC | اپ build شود، به سرور自分 وصل شود، لاگین + چت ۱:۱ + ارسال عکس | **۱–۲ ماه** | ۲–۳ senior |
| MVP | چت ۱:۱/گروهی، média, voice, push自分، پروفایل — روی سرور ایران، کارا در نت ملی | **۶–۱۲ ماه** | ۶–۸ نفر |
| Featureهای بیشتر | کانال، ربات، تماس، استوری... | ماه‌ها |-routing |
| parity کامل با تلگرام | — | **چند سال** | تیم بزرگ |

> توجه: «همه امکانات در ۲ ساعت» **ممکن نیست** — سرور تلگرام closed-source و حاصل سال‌ها کار تیم بزرگ است.

## ۸. نکات legal / branding

- کد GPL است → سورس fork باید عمومی بماند ( ✓ همین现在 public است)
- از اسم «Telegram» و لوگوی paper-plane استفاده نکنید (طبق README)
- هاست داده در ایران با الزامات 데이터 internal هم‌راستاست؛ Koordinatsia با نهادهای ذی‌ربط (وزارت ICT/ساترا) جداگانه است

## ۹. وضعیت فعلی — PoC کار می‌کند ✅

**`server/mtserver/`** — سرور MTProto自分 (Python, ~۱۵۰۰ خط) که همان پروتکل MTProto 2.0 را صحبت می‌کند که اپ اندروید این ریپو (`jni/tgnet/Handshake.cpp`) استفاده می‌کند:

- handshake کامل: `req_pq_multi` → `req_DH_params` → `set_client_DH_params` → `dh_gen_ok` (کلید RSA自分، fingerprint自分)
- transport رمزنگاری‌شده: AES-256-IGE + SHA-256 msg_key + container
- متدهای پیاده‌شده: `help.getConfig`, `ping`, `auth.sendCode/signIn/signUp/logOut`,
  `updates.getState`, `messages.sendMessage/getHistory/getDialogs/readHistory/receivedMessages/getDialogFilters`,
  **`messages.sendMedia` (عکس) + `upload.saveFilePart/saveBigFilePart` + `upload.getFile` (download)** ✅
- تست عکس end-to-end پاس شد: آپلود ۲۰۰KB → sendMedia → push به کاربر دوم → تاریخچه → دانلود با `upload.getFile` (content match=True)
- صفحه‌ی statusSurvey JSON: `--http-status-port 8080` → `GET /` = وضعیت زنده (users/messages/files/uptime)
- سرور live شده: `python3 -m mtserver.main --listen-host 0.0.0.0 --listen-port 8443 --public-host <IP سرور> --public-port 8443 --http-status-port 8080`
- **تست end-to-end پاس شد** (`server/test_client.py` — کلاینت تست با همان پروتکل): handshake + لاگین دو کاربر + ارسال پیام + **push زنده به سشن کاربر دیگر** + تاریخچه + دیالوگ‌ها → `ALL TESTS PASSED ✅`
- اجرا: `PYTHON=/path/to/venv/bin/python bash server/run_demo.sh [port]`
  (وابستگی‌ها: `pip install -r server/requirements.txt` — telethon + cryptography)
- باگ‌های رفع‌شده در این Gods session: `tlmini.tbytes` (ترتیب بایت طول ۲۵۶)، `sha1().digest()` در `crypto.py`,
  `struct.pack("<Bq")`، طول هدر پیام (۳۲ بایت)،Shipping dubbele length-prefix در کلاینت تست،
  `msg_container` بدون vector-ctor، parse کردن `rpc_result.body`، و split دیالوگ `"d1_2"`

**Patchهای اپ اندروید:**
- `BuildVars.java`: اضافه شدن `USE_CUSTOM_BACKEND` + `CUSTOM_BACKEND_BASE_URL`
- **بخش بعدی:** ساخت `TMessagesProj/jni/tgnet/CustomBackendConfig.h` (سرور خودش snippet آن را Xeon print می‌کند)
  و سیم‌کشی آن در `ConnectionsManager.cpp` / `Handshake.cpp` تا اپ واقعی به سرور自分 وصل شود

## ۱۰. fase بعدی (به ترتیب)

1. ✅ **پچ native کلاینت اندروید انجام شد** — `TMessagesProj/jni/tgnet/CustomBackendConfig.h` ساخته شد و در
   `ConnectionsManager.cpp` (DC自分 + default DC) و `Handshake.cpp` (کلید RSA自分) سیم‌کشی شد با `#if CUSTOM_BACKEND`.
   برای build: مقدار關ی که `mtserver` در استارتاپ print می‌کند را در `CustomBackendConfig.h` paste کنید
   (`CUSTOM_BACKEND 1` + host/port/fingerprint/PEM سرور自تان)، بعد با Android Studio پروژه را build کنید.
   نکته: build این پروژه needs Android SDK 36 + NDK 27 — روی سیستم توسعه‌دهنده انجام می‌شود، نه در sandbox.
2. CZ扩大 metدها در `server/mtserver/api.py` (گروه‌ها، média, bots, کانال‌ها...) — هر feature = یک handler + تست در `test_client.py`
3. زیرساخت ایران: هاست روی VPS ایرانی، دامنه `.ir`، TLS، DDoS protection، SMS gateway، push自分 (WebSocket持久)، حذف Firebase/Google (GcmPushListenerService و play-services)
4. distribUSION: APK مستقیم + بازار/مایکت

## ۱۰. @@questions باز برای تصمیم‌گیری

1. Scope نسخه اول (MVP) چیست؟ — چت ۱:۱ + گروه + média؟ + کانال؟ + تماس؟
2. اندازه و ترکیب تیم自分؟
3. تکنولوژی بک‌اند: از صفر (custom WebSocket API) یا روی Signal Server / Matrix?
4. زیرساخت: کدام ارائه‌دهنده VPS/کلود ایرانی (آسیاتک، همتا، پارس‌پک...)؟
