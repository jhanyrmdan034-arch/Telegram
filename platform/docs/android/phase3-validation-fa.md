# اعتبارسنجی فاز ۳ — محیط کامپایل، اقدامات و نتیجه

تاریخ: ۲۰۲۶-۱۱-۰۴ · شاخه: `arena/76249c66-telegram`

## ۱. وضعیت کامپایل اندروید (اندازه‌گیری‌شده، نه حدس)

| بررسی | نتیجه |
|---|---|
| `which java javac sdkmanager` | هیچ‌کدام نصب نیست |
| `ls /usr/lib/jvm` | وجود ندارد |
| `echo $ANDROID_HOME` / `$ANDROID_SDK_ROOT` / `$JAVA_HOME` | همه خالی |
| `pip install jdk4py` | نصب شد، اما **فقط runtime**: `bin/` شامل `java`، `keytool`، … و **بدون `javac`** |
| `curl https://dl.google.com` | `000` (بدون دسترسی) |
| `curl https://repo1.maven.org` | `000` |
| `curl https://services.gradle.org` | `000` |
| `curl https://api.adoptium.net` | `000` |
| `./gradlew --offline tasks` | `SSLHandshakeException` در `org.gradle.wrapper.Download` — wrapper نمی‌تواند توزیع Gradle 8.13 را بگیرد |

**نتیجه:** در این محیط هیچ مسیری برای نصب JDK کامل (با `javac`)، Android SDK (`android.jar`, `aapt2`, `d8`)
و وابستگی‌های AGP از Maven/Google وجود ندارد. بنابراین **کامپایل، ساخت APK و اجرای Emulator انجام نشد** —
نه به‌خاطر خطای کد، بلکه به‌خاطر نبود Toolchain و بسته‌شدن شبکه.

### راه‌حل‌های ممکن برای رسیدن به APK
1. اجرای workflow آماده در GitHub Actions توسط شما (تب Actions → Run workflow) — محیطش Android SDK دارد.
2. اجرا روی ماشین با Android Studio (JDK 17 + SDK 36 + NDK 27.2.12479018).
3. باز کردن دسترسی خروجی به `dl.google.com`, `repo1.maven.org`, `services.gradle.org`, `api.adoptium.net`
   در سندباکس؛ در آن صورت کل زنجیره (JDK + SDK + Gradle) از داخل همین محیط قابل نصب است.

## ۲. کاری که در این مرحله انجام شد

### اتصال کامل اکشن‌های منو به Repository
قلاب‌ها نه در منو، بلکه در **نقاط گلوگاه** `MessagesController` گذاشته شدند تا همهٔ مسیرها
(منوی نگه‌داشتن، Swipe، Undo، پیش‌نمایش چت) پوشش داده شوند:

| متد | رفتار وقتی `DIALOGS_FROM_PLATFORM` روشن است |
|---|---|
| `pinDialog(long, boolean, InputPeer, long)` | `PlatformDialogActions.setPinned` → `POST /chats/{id}/pin` |
| `markDialogAsRead(...)` | `PlatformDialogActions.markRead` → `POST /chats/{id}/read?seq=` |
| `deleteDialog(long, int, boolean)` | چت خصوصی → آرشیو (نیمهٔ تلگرام)، گروه/کانال → `DELETE /chats/{id}` |
| `addDialogToFolder(long, int, …)` | `folderId == 1` یعنی آرشیو → `POST /chats/{id}/archive` |

`PlatformDialogActions` شناسهٔ چت را از نقشهٔ `PlatformDialogsSync.chatUuidFor(dialogId)` می‌گیرد،
پس چت خصوصی (که `dialogId` آن شناسهٔ کاربر است) هم درست آدرس‌دهی می‌شود و درخواست به شیء اشتباه نمی‌رود.

### پرچم `out` در پیش‌نمایش
`buildPreview` اکنون شناسهٔ فرستنده را با کاربر جاری مقایسه می‌کند
(`UserConfig.getCurrentUser().id` بعد از نگاشت UUID→long) و `toMessage(..., out)` را درست می‌دهد؛
پس پیشوند «You:» درست نمایش داده می‌شود.

### پوشه‌های شامل مخاطبان
قبلاً پوشه فقط چت‌های گروه/کانال را پیدا می‌کرد، چون شناسهٔ دیالوگ چت خصوصی منفیِ شناسهٔ چت نیست.
اکنون نقشهٔ «شناسهٔ چت بک‌اند → dialogId» از خود ردیف‌ها ساخته می‌شود، بنابراین پوشه شامل مخاطب هم کار می‌کند.

### آواتار و اعتبار کش
- سرور محتوا اکنون `ETag` (sha256 فایل) و `Cache-Control: max-age=0, must-revalidate` می‌دهد؛
  `If-None-Match` با مقدار درست → **۳۰۴** و کلاینت همان فایل کش‌شده را نگه می‌دارد.
- آپلود جدید = فایل جدید = شناسهٔ جدید = کلید کش جدید؛ `PlatformAvatars` هنگام تغییر شناسه،
  بیت‌مپ و فایل و sidecar قدیمی را پاک می‌کند.

### اثبات نبود MTProto در مسیر فهرست
`tools/check_dialogs_no_mtproto.py` (جدید) به‌صورت ایستا بررسی می‌کند که:
1. `DialogsActivity.loadDialogs` قبل از `messagesController.loadDialogs(...)` برمی‌گردد؛
2. در هر چهار متد بالا، شاخهٔ پلتفرم **قبل از** اولین نشانهٔ MTProto (`sendRequest`/`ConnectionsManager`/`MessagesStorage`) است؛
3. فایل‌های `platform/tl/*` هیچ نماد MTProto ندارند.
خروجی فعلی: `OK: the dialog list and its actions never reach MTProto`.

## ۳. تست‌ها

| مورد | نتیجه |
|---|---|
| `tests/test_avatar_cache.py` (جدید) | ۱ تست: شناسهٔ جدید آواتار، ETag متفاوت، `304` روی محتوای بدون تغییر |
| کل بک‌اند | **۱۳۴ سبز** (۱۲۴ اصلی + ۹ فاز ۳ + ۱ آواتار) |
| `check_dialogs_no_mtproto.py` | OK |
| `check_android_phase1.py` / `check_tl_contract.py` | OK |

## ۴. آنچه هنوز تأیید نشده
1. **هیچ کامپایل/APK/امولاتور** — دلیل و سه راه‌حل در بخش ۱.
2. بنابراین هیچ‌کدام از موارد «داخل خود اپ» (رندر ردیف، آواتار، پوشه، منو، WebSocket در اپ) **تأیید نشده**؛
   تنها قرارداد داده در سطح API/تست اثبات شده است.
3. باز کردن گفتگو شناسهٔ درست را می‌دهد، اما محتوای `ChatActivity` تا فاز ۴ از MTProto می‌آید.
4. وجود `MessagesController` در مسیر پلتفرم عمدی است (UI داده را از آن می‌خواند)؛ حمل‌ونقل آن استفاده نمی‌شود.
