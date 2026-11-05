# بررسی سورس‌کد — Telegram for Android

**تاریخ بررسی:** ۲۰۲۶-۱۱-۰۴ · **نسخه:** 13.1.1 (build 7237) · **حجم مخزن بدون ساب‌ماژول‌ها:** ۵۵۸ مگابایت

---

## ۱) خلاصهٔ اجرایی

این مخزن **نسخهٔ رسمی سورس تلگرام اندروید (DrKLO/Telegram)** است و **هیچ تغییری روی آن اعمال نشده**.

صحت این ادعا با مقایسهٔ تک‌تک هش‌های blob درخت گیت با مخزن مرجع تأیید شد:

| بررسی | نتیجه |
|---|---|
| کامیت فعلی | `3de37c8cfb` — همان هش کامیت مرجع (`update to 13.1.1 (7237)`) |
| فایل‌های تغییریافته | **۰** |
| فایل‌های اضافه‌شده (احتمال تزریق کد) | **۰** |
| فایل‌های حذف‌شده | **۰** |
| ساب‌ماژول‌ها (gitlink) | ۲۰ مورد، همه با هش درست ولی **خالی/دانلودنشده** |

> جمع‌بندی: از نظر اصالت و امنیت زنجیرهٔ تأمین، **سورس پاک است**؛ هر آنچه در ادامه می‌آید ایرادهای ذاتی و شناخته‌شدهٔ خودِ تلگرام است، نه دستکاری در این کپی.

⚠️ **مهم‌ترین نکتهٔ عملی:** در وضعیت فعلی این چک‌اوت **کامپایل نمی‌شود**، چون تمام ساب‌ماژول‌ها (که بخش نیتیو و کتابخانهٔ مدیا روی آن‌ها سوار است) دانلود نشده‌اند. جزئیات در بخش ۵.

---

## ۲) نقشهٔ ساختار پروژه

```
Telegram/
├── build.gradle                  ← AGP 8.13.2، Kotlin 2.1.0، مخازن Google/Huawei
├── settings.gradle               ← ۶ ماژول + درج media3 از ساب‌ماژول
├── gradle.properties             ← نسخه، امضای APK، فلگ‌های R8
├── buildSrc/                     ← پلاگین‌های اختصاصی Gradle (Kotlin)
├── TMessagesProj/                ← هستهٔ برنامه (AAR)
│   ├── src/main/java/org/telegram/{messenger,ui,tgnet,SQLite,utils}
│   ├── src/main/res/             ← ۳۹۲۶ فایل webp، لایوت‌ها، انیمیشن‌های Lottie (json)
│   ├── jni/                      ← کد C/C++ (CMake) + prebuilt
│   ├── config/                   ← AndroidManifest نسخه‌ها + release.keystore دامی
│   └── libs/                     ← libgsaverification-client.aar
├── TMessagesProj_App/            ← APK اصلی (بسته‌بندی از Play)
├── TMessagesProj_AppHuawei/      ← نسخهٔ AppGallery (AGConnect)
├── TMessagesProj_AppStandalone/  ← نسخهٔ وب/بدون Play Services
├── TMessagesProj_AppHockeyApp/   ← نسخهٔ قدیمی توزیع بتا
├── TMessagesProj_AppTests/       ← تست‌های instrumented (مدل‌های TdLib)
├── TMessagesProj_Modules/media/  ← فورک media3 اگزوناپلیر (ساب‌ماژول)
└── Dockerfile                    ← بیلد خودکار با SDK 36 + NDK 27.2
```

### لایه‌های معماری

| لایه | پکیج | توضیح |
|---|---|---|
| شبکه | `org.telegram.tgnet` | پیاده‌سازی MTProto، `ConnectionsManager`، رمزنگاری/کلیدها |
| منطق دامنه | `org.telegram.messenger` | `MessagesController`, `MessagesStorage` (کش SQLite)، `MediaController`, `NotificationCenter` (event bus اختصاصی) |
| رابط کاربری | `org.telegram.ui` | ۲۳۵ فایل در ریشه + زیرپکیج‌های `Cells`, `Components`, `Stories`, `Business`, `Charts`, `Wallet` |
| ذخیره‌سازی محلی | `org.telegram.SQLite` | wrapper سبک روی SQLite بومی |
| نیتیو | `jni/` | `tmessages.49`, WebRTC/VoIP، FFmpeg، BoringSSL، TDLib، tgcalls، OpenH264، dav1d، WAMR |

---

## ۳) آمار کد

| بخش | حجم |
|---|---|
| جاوا (`org.telegram.*`) | **۱٫۵۸ میلیون خط** در ۲٫۲۶۸ فایل |
| Kotlin | ۷۱۷ فایل — ۶۸۱ مورد آن کلاس‌های **تولیدشده** تست TdLib است |
| نیتیو (C/C++/headers) | حدود **۱٫۵ میلیون خط** (سهم اصلی: `jni/voip/webrtc`) |
| منابع | ۳۹۲۶ webp، ۶۵۴ XML، ۴۰۳ PNG |
| تست | فقط `TMessagesProj_AppTests` (تست مدل‌های شبکه) — بدون تست واحد UI |

**بزرگ‌ترین فایل‌ها (نشانهٔ بدهی فنی):**

| فایل | خط |
|---|---|
| `tgnet/TLRPC.java` (تولیدشده از TL schema) | ۷۱٫۵۵۷ |
| `ui/ChatActivity.java` | ۴۷٫۶۹۹ |
| `ui/Cells/ChatMessageCell.java` | ۲۹٫۶۱۵ |
| `messenger/MessagesController.java` | ۲۵٫۶۴۱ |
| `ui/PhotoViewer.java` | ۲۳٫۸۳۱ |

---

## ۴) پیکربندی بیلد

| مورد | مقدار |
|---|---|
| Gradle / AGP / Kotlin | 8.13 / 8.13.2 / 2.1.0 |
| compileSdk / targetSdk | 36 / 36 |
| minSdk | **23** (اندروید ۶) |
| Build Tools / NDK / CMake | 36.0.0 / 27.2.12479018 / 3.22.1 |
| زبان جاوا | Java 8 + `desugar_jdk_libs` 2.1.5 |
| بهینه‌سازی | R8 full mode، shrinkResources، ABI: arm64-v8a, armeabi-v7a, x86, x86_64 |
| نسخه‌گذاری | `versionCode = APP_VERSION_CODE × 10 + abiVersionCode` |

نکات قابل توجه در بیلد:

- `variantFilter` فقط `afat` را برای buildTypeهای غیر release فعال می‌کند؛ بقیه نادیده گرفته می‌شوند.
- یک بلوک `subprojects {}` بومی در `build.gradle` ریشه، فونت‌های اضافی JLatexMath را از APK حذف می‌کند (بهینه‌سازی دستی حجم).
- `android.enableJetifier=true` با AGP 8.x صرفاً سرباز اضافه است و می‌شود حذفش کرد.
- `buildSrc` شامل تسک‌های اختصاصی است: تولید رشته‌های محلی‌سازی، متادیتای Lottie، و تولید آی‌دی منابع — یعنی جریان بیلد کاملاً سفارشی و غیراستاندارد است.

---

## ۵) وضعیت این چک‌اوت — چرا الان بیلد نمی‌شود

۱. **ساب‌ماژول‌ها خالی‌اند.** هر ۲۰ مسیر زیر بدون محتوا هستند:

```
jni/td · jni/tgcalls · jni/tlottie · jni/wallet-engine
jni/third_party/{ffmpeg, boringssl, dav1d, libvpx, libyuv, openh264,
                 absl, wamr, libprisma, boost-regex, xiph/{ogg,opus,opusfile}}
lib/jlatexmath · TMessagesProj_Modules/media
```

بدون این‌ها نه CMake target `tmessages.49` ساخته می‌شود، نه `:jlatexmath` و نه `media3` در `settings.gradle` resolve می‌شود → **Gradle Sync شکست می‌خورد**.

۲. **کلون shallow است** (`.git/shallow`, عمق ۱) → تاریخچه، blame و مقایسهٔ نسخه‌ها موجود نیست.

۳. در این محیط بیلد، **JDK و Android SDK/NDK نصب نیست** (`java` موجود نیست، `ANDROID_HOME` خالی) و دسترسی شبکه هم فقط به چند دامنه محدود است؛ بنابراین بیلد کامل اینجا ممکن نیست.

### دستور راه‌اندازی

```bash
git submodule update --init --recursive --depth=1   # ~چند گیگابایت دانلود
# سپس: JDK 17 + Android SDK 36 + NDK 27.2.12479018 + CMake 3.22.1
./gradlew :TMessagesProj_App:assembleAfatRelease
```

برای بیلد بدون دستکاری محیط، `Dockerfile` موجود مسیر آماده‌ای است (`gradle:8.13-jdk17` + نصب SDK/NDK + خروجی APK و mapping).

---

## ۶) نکات امنیتی و پیکربندی

| مورد | وضعیت | توضیح |
|---|---|---|
| `release.keystore` | 🔴 دامی/عمومی | همان keystore قابل بازتولید رسمی؛ رمز `android`. هرگز برای انتشار استفاده نشود. |
| `BuildVars.APP_ID = 4` و `APP_HASH` | 🔴 مال تلگرام | استفاده از api_id رسمی برای فورک، نقض شرایط API است؛ باید `api_id` خودتان را بگیرید. |
| `google-services.json` + کلیدهای Firebase | 🟠 مربوط به پروژهٔ تلگرام | FCM/AppIndexing برای فورک کار نمی‌کند؛ باید جایگزین شود. |
| `SUPPORTS_PASSKEYS = true` | 🟠 فقط با app id رسمی | در فورک باید false شود. |
| `getSmsHash()` | 🟠 مقدار هاردکدشده | SMS Retriever در فورک از کار می‌افتد. |
| `android:allowBackup="true"` | 🟠 | امکان استخراج دادهٔ اپ با adb؛ برای فورک بهتر است `dataExtractionRules` تعریف شود. |
| `android:usesCleartextTraffic="true"` | 🟢 عامدانه | برای مرورگر داخلی و لینک‌های `http://`؛ ترافیک API خودِ اپ همیشه MTProto/TLS است. |
| ۳۰ کامپوننت `exported="true"` از ۶۳ کامپوننت | 🟢 طبیعی | ویجت‌ها، `OpenChatReceiver`، سرویس‌های پخش موسیقی/تماس و …؛ در صورت تغییر برند بازبینی شود. |
| `BACKUP`/`SYSTEM_ALERT_WINDOW`/`REQUEST_INSTALL_PACKAGES` | 🟢 موردنیاز | برای نصب آپدیت APK و حباب‌های چت لازم‌اند. |
| اسکن الگوهای مخرب | 🟢 پاک | هیچ `DexClassLoader` مخفی، `Runtime.exec` مشکوک یا آدرس مقصد ناشناس پیدا نشد. تنها سه هیت اولیه (`eval(` در `res/raw/*.json`) محتوای **انیمیشن Lottie** است و در نسخهٔ مرجع هم عیناً وجود دارد. |

---

## ۷) کیفیت کد — مشاهدات

**قوت‌ها**
- جداسازی خوب لایهٔ شبکه (`tgnet`) از منطق و UI؛ MTProto مستقل و قابل تست.
- کش محلی کامل (SQLite + مدل رویدادمحور) که آفلاین‌فرست است.
- استفاده از R8 full mode + shrinking و حذف دارایی‌های غیرلازم برای کنترل حجم.
- پوشش چهار ABI و چند کانال توزیع (Play / Huawei / Standalone) با یک هسته.

**ضعف‌ها / نقاط درد برای نگهداری**
- فایل‌های غول‌آسا: `ChatActivity` نزدیک ۵۰ هزار خط، `ChatMessageCell` ۳۰ هزار خط → تغییرات پرریسک، merge سخت.
- `TLRPC.java` تولیدشده و کامیت‌شده (۷۱k خط) → هر بار دیف‌های عظیم در گیت.
- ۳۲۳ مورد `TODO/FIXME` در کد جاوا.
- کپی محلی `androidx.recyclerview` (۱۳٫۶k خط) → در هر ارتقای اندروید باید دستی همگام شود.
- تقریباً بدون تست خودکار و بدون هیچ CI در مخزن (بدون `.github/`).
- وابستگی‌های نسخه‌دار به‌صورت رشته‌ای و پراکنده؛ بدون version catalog.
- چند وابستگی قدیمی/حساس به امنیت: `stripe-android:2.0.2` (بسیار قدیمی)، `nanohttpd:2.3.1`، `play-services-safetynet` (منسوخ به نفع Play Integrity).

---

## ۸) مجوز و الزامات فورک

- مجوز: **GNU GPL v2 یا بالاتر** (فایل `LICENSE`).
- الزامات تلگرام برای ساخت اپ مشتق‌شده:
  1. `api_id`/`api_hash` اختصاصی بگیرید (core.telegram.org/api/obtaining_api_id).
  2. از نام «Telegram» برای اپ خودتان استفاده نکنید یا صریحاً غیررسمی بودن را مشخص کنید.
  3. از لوگوی استاندارد (هواپیمای کاغذی سفید روی دایرهٔ آبی) استفاده نکنید.
  4. راهنمای امنیتی MTProto را رعایت کنید و **سورس خودتان را هم منتشر کنید**.
- توجه: بستهٔ `debug` پسوند `.beta` می‌گیرد که همان `org.telegram.messenger.beta` رسمی است → کنار نسخهٔ رسمی بتا نصب نمی‌شود.

---

## ۹) پیشنهاد گام بعدی

1. **اگر هدف فقط مطالعه است:** سورس تمیز و کامل است؛ از `messenger/` و `tgnet/` شروع کنید.
2. **اگر هدف بیلد است:** ساب‌ماژول‌ها را init کنید و از `Dockerfile` استفاده کنید (سریع‌ترین مسیر قابل تکرار).
3. **اگر هدف فورک/برندسازی است:** چک‌لیست حداقلی →
   `BuildVars` (APP_ID/HASH، SafetyNet، Passkeys)، `gradle.properties` (APP_PACKAGE، کلید امضا)، `google-services.json`، آیکون/نام/تم، و حذف وابستگی‌های بلااستفاده مثل Stripe قدیمی.
4. **اگر هدف بازبینی امنیتی عمیق است:** تمرکز روی `tgnet/ConnectionsManager`، `secretmedia/`, `FileLoadOperation`، `MediaController` و مسیر آپلود فایل؛ به‌علاوه بازبینی `usesCleartextTraffic` و `allowBackup`.
