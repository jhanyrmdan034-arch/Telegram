# فاز ۶.۲ — نتیجهٔ Build واقعی Android (GitHub Actions)

> این سند فقط نتیجهٔ **اجرای واقعی** CI را ثبت می‌کند. هیچ موردی حدس زده نشده است.

## ۱) دسترسی CI

| مورد | نتیجه |
|---|---|
| `gh workflow run` (workflow_dispatch) | ❌ 403 – نیازمند پرمیشن `actions=write` (از هدر `X-Accepted-Github-Permissions` تأیید شد) |
| push برنچ توسط GitHub App | run ایجاد نمی‌کند |
| **PR + trigger `pull_request`** | ✅ **کار می‌کند** — با هر push به برنچ، Workflow «Build Telegram APK» اجرا می‌شود (PR #1) |

## ۲) نتیجهٔ کامپایل (اجرای واقعی)

| ران | Compile Java sources |
|---|---|
| `41985868648` | ✅ موفق |
| `41993496049` | ✅ موفق |
| `42001642614` (با اصلاحات امولاتور) | در حال اجرا |

- ابزار: JDK 17 (temurin) + Android SDK 36 + Gradle؛ دستور:
  `./gradlew :TMessagesProj:compileDebugJavaWithJavac` (با fallback به `compileAfatDebugJavaWithJavac`).
- **این اولین کامپایل واقعی پروژه پس از همهٔ تغییرات پلتفرم بود** و سه خطای واقعی را آشکار کرد که
  همه اصلاح شدند:
  1. `MediaRepository` سازندهٔ `PlatformException(String,int,Throwable)` را صدا می‌زد که وجود نداشت
     → سازندهٔ سازگار به `PlatformException` اضافه شد.
  2. `PlatformException.kindForStatus` از پکیج `net` قابل دسترس نبود → `public` شد.
  3. `PlatformAuthInterceptor.intercept` از `ConnectionsManager.RequestDelegate` استفاده می‌کرد
     در حالی که `RequestDelegate` یک interface مستقل در `org.telegram.tgnet` است.
  4. `SearchAdapter` (فاز ۵) `Callback<ArrayList<User>>` به متدی می‌داد که `Callback<List<User>>`
     می‌خواهد → نوع اصلاح شد.
- یک مانع محیطی هم در خود CI برطرف شد: job کامپایل ساب‌ماژول‌های
  `TMessagesProj_Modules/media` و `TMessagesProj/lib/jlatexmath` را checkout نمی‌کرد و
  Gradle با `MissingResourceException: core_settings.gradle` شکست می‌خورد.

## ۳) نتیجهٔ ساخت APK (اجرای واقعی)

| ران | Build APK | آرتیفکت |
|---|---|---|
| `41985868648` | ✅ موفق | `Telegram-apk-arm64` — ۳۷٫۶۸۶٫۹۷۶ بایت |
| `41993496049` | ✅ موفق | `Telegram-apk-arm64` — ۳۷٫۶۸۶٫۹۶۹ بایت (id: `13401493442`) |

- دستور: `./gradlew :TMessagesProj_App:assembleAfatRelease` (arm64 فقط، پکیج `org.telegram.messenger.fork`).
- خروجی داخل ران: `Telegram-13.1.1-arm64.apk` + `Telegram-13.1.1-arm64.apk.sha256`.

**لینک دانلود آرتیفکت (فقط از حساب مالک مخزن):**
`https://github.com/jhanyrmdan034-arch/Telegram/actions/runs/41993496049/artifacts/13401493442`
(یا: Actions → Build Telegram APK → run → بخش Artifacts → `Telegram-apk-arm64`)

**محدودیت سندباکس:** دانلود خودِ فایل zip از این محیط ممکن نیست، چون GitHub آرتیفکت‌ها را از
`productionresultssa*.blob.core.windows.net` سرو می‌کند و این دامنه در allowlist شبکهٔ سندباکس
نیست (خطای `EOF`). آرتیفکت `expired=false` است و با حساب مالک قابل دانلود.

## ۴) نتیجهٔ Emulator smoke test

| ران | نتیجه | گام شکست‌خورده | علت واقعی (از annotation) |
|---|---|---|---|
| `41985868648` | ❌ | Build debug APK | لاگ در دسترس نبود |
| `41993496049` | ❌ | Build debug APK | `processAfatDebugGoogleServices` → «No matching client found for package name 'org.telegram.messenger.fork.beta'» |
| `42001642614` | 🔄 | — | اصلاح اعمال شد و در حال اجراست |

**تحلیل علت ریشه‌ای:** در job امولاتور، پکیج به `org.telegram.messenger.fork` تغییر می‌کرد، اما
نوع debug در `TMessagesProj_App/build.gradle` پسوند `applicationIdSuffix ".beta"` دارد؛
پس applicationId واقعی `org.telegram.messenger.fork.beta` می‌شد که هیچ کلاینتی در
`google-services.json` برایش وجود ندارد. هم‌زمان گام «Install and drive» اپ را با پکیج
`org.telegram.messenger.fork` اجرا می‌کند، پس حتی با بیلد موفق هم نصب/اجرا نمی‌شد.

**اصلاح (فقط داخل CI، بدون تغییر اپ):**
1. حذف پسوند debug در runner: `sed -i 's/applicationIdSuffix ".beta"//' TMessagesProj_App/build.gradle`
2. افزودن کلاینت `org.telegram.messenger.fork` به همهٔ `google-services.json`ها.
3. افزودن گام گزارش خطا به‌صورت annotation تا دفعهٔ بعد علت بدون لاگ هم خوانده شود.

## ۵) وضعیت `LOCAL_FIRST_MEDIA`

`BuildVars.LOCAL_FIRST_MEDIA = false` **باقی می‌ماند**. کد مسیر Local-First نوشته و کامپایل شده،
اما تا زمانی که یک اجرای واقعی روی دستگاه/امولاتور آن را تأیید نکند، خاموش است.

## ۶) قابلیت‌هایی که هنوز در APK آزمایش نشده‌اند

| قابلیت | وضعیت واقعی |
|---|---|
| ارسال/دریافت عکس در اپ | کامپایل شده، **در دستگاه آزمایش نشده** |
| نمایش تصویر داخل حباب | ساختار TL ساخته می‌شود، **دیده نشده** |
| ارسال/دریافت ویدیو و پخش | مسیر ارسال کامپایل شده، پخش پیاده نشده |
| سند و دانلود | API تست دارد، در اپ آزمایش نشده |
| نوار پیشرفت آپلود/دانلود در حباب | **پیاده نشده** (فقط `uploadProgress`) |
| لغو و تلاش مجدد در UI | وصل شده در کد، **در دستگاه آزمایش نشده** |
| Local-First (حباب قبل از آپلود) | کامپایل شده، **فلگ خاموش** |
| پیش‌نمایش ویدیو/دوربین | وجود ندارد |
| استیکر/ایموجی سفارشی | عمداً پیاده نشده |

## ۷) جمع‌بندی

- **کامپایل واقعی: موفق ✅**
- **APK Release: ساخته شد ✅** (آرتیفکت قابل دانلود برای مالک، از سندباکس غیرقابل دانلود)
- **Emulator: تا این لحظه ناموفق بوده؛ علت ریشه‌ای پیدا و اصلاح شد؛ ران تأییدی در جریان است.**
- اجرای واقعی اپ و تست دو حساب (Alice/Bob) هنوز **انجام نشده** و ادعا نمی‌شود.
