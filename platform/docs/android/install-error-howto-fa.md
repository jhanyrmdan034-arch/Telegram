# استخراج خطای دقیق نصب APK (Android 10 / ARMv7)

پیام «برنامه نصب نشد» پیام عمومیِ PackageInstaller اندروید است و **کد واقعی خطا** را
نشان نمی‌دهد. کد واقعی (`INSTALL_FAILED_*` / `INSTALL_PARSE_FAILED_*`) فقط در
`adb` یا لاگ سیستم دیده می‌شود. این سند سه روش عملی برای گرفتن همان کد را توضیح می‌دهد.

---

## روش ۱ (دقیق‌ترین): اسکریپت آماده روی کامپیوتر

پیش‌نیاز: `adb` (Android Platform Tools) و فعال بودن «USB debugging» روی گوشی.

```bash
# در ریشهٔ مخزن:
./tools/adb-install-diagnose.sh Telegram-13.1.1-armv7.apk
```

اسکریپت این‌ها را جمع می‌کند و در `install-diagnose-report.txt` می‌نویسد:

| بخش | چه چیزی استخراج می‌شود |
| --- | --- |
| دستگاه | مدل، نسخهٔ اندروید (API)، `ro.product.cpu.abilist` |
| پشتیبانی ۳۲ بیتی | آیا `armeabi-v7a` در `abilist` هست |
| بسته‌های مزاحم | آیا `org.telegram.messenger.fork` قبلاً نصب است + اثر انگشت امضا |
| نصب | خروجی کامل `adb install -r -d` + کد خروج |
| نصب جلسه‌ای | `pm install-create` / `install-write` / `install-commit` (رشتهٔ خام خطا) |
| لاگ | `logcat` فیلترشده روی `PackageManager`, `PackageInstaller`, `installd` |
| حکم | نگاشت خودکار به علت (جدول پایین) |

اگر `adb` ندارید، همان دستورها را دستی بزنید؛ کافی است خط اول را داشته باشیم:

```bash
adb install -r -d Telegram-13.1.1-armv7.apk
```

---

## روش ۲ (بدون کامپیوتر): نصب با SAI

برنامهٔ **Split APKs Installer (SAI)** کد خطای خام PackageManager را نمایش می‌دهد
(برخلاف نصب‌کنندهٔ پیش‌فرض اندروید). APK را با SAI باز کنید و پیام خطای آن را
عیناً بفرستید. برنامه‌های logcat معمولی روی اندروید ۱۰ بدون root به لاگ سیستم
دسترسی ندارند، بنابراین SAI کوتاه‌ترین مسیر بدون کامپیوتر است.

---

## روش ۳: بررسی‌های دستی سریع روی گوشی

1. **بستهٔ قبلی:** تنظیمات → برنامه‌ها → جست‌وجوی «Telegram». اگر نسخه‌ای با نام
   فورک نصب است، حذفش کنید. تلگرام رسمی (`org.telegram.messenger`) با فورک
   (`org.telegram.messenger.fork`) دو بستهٔ جدا هستند و حذف یکی روی دیگری اثر ندارد.
2. **معماری:** تنظیمات → دربارهٔ گوشی → پردازنده. یا در «CPU-Z» مقدار
   `Supported ABIs` را ببینید؛ باید `armeabi-v7a` داشته باشد.
3. **صحت فایل:** حجم فایل باید دقیقاً `36,476,361` بایت باشد و
   `sha256` آن با فایل `Telegram-13.1.1-armv7.apk.sha256` داخل همان آرتیفکت یکی باشد.
4. **نصب از منابع ناشناس:** تنظیمات → برنامه‌ها → دسترسی ویژهٔ برنامه‌ها →
   «نصب برنامه‌های ناشناس» → مرورگر/فایل‌منیجر باید مجاز باشد.
5. **Play Protect:** فروشگاه Play → تصویر پروفایل → Play Protect → تنظیمات →
   «اسکن برنامه‌ها» را موقتاً خاموش کنید (فقط برای آزمون).

---

## جدول تفسیر کد خطا

| کد خطا | علت | اصلاح |
| --- | --- | --- |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | نسخهٔ دیگری از همان بسته با امضای متفاوت نصب است | حذف بستهٔ قبلی |
| `INSTALL_FAILED_NO_MATCHING_ABIS` | گوشی کد ۳۲ بیتی ARM را اجرا نمی‌کند | استفاده از نسخهٔ `arm64-v8a` |
| `INSTALL_PARSE_FAILED_NO_CERTIFICATES` | APK امضا ندارد | بازسازی با امضا |
| `INSTALL_PARSE_FAILED_NOT_APK` | فایل APK نیست / ناقص است (زیپ یا دانلود نیمه‌کاره) | دانلود دوباره + تطبیق sha256 |
| `INSTALL_FAILED_INVALID_APK` | آرشیو خراب یا کتابخانهٔ native ناسازگار | بازسازی بسته |
| `INSTALL_FAILED_INSUFFICIENT_STORAGE` | فضای کافی نیست (نصب به ~۲ برابر حجم نیاز دارد) | آزادسازی فضا |
| `INSTALL_FAILED_TEST_ONLY` | خروجی با پرچم testOnly ساخته شده | ساخت release واقعی |
| `INSTALL_FAILED_VERIFICATION_FAILURE` | Play Protect / verifier سیستم | خاموش‌کردن موقت تأیید |
| `INSTALL_FAILED_VERSION_DOWNGRADE` | نسخهٔ نصب‌شده `versionCode` بزرگ‌تری دارد | حذف نسخهٔ قبلی |

---

## وضعیت تأییدشدهٔ APK فعلی (اندازه‌گیری‌شده در CI)

| ویژگی | مقدار | منبع |
| --- | --- | --- |
| بسته | `org.telegram.messenger.fork` | `aapt2 dump badging` |
| versionCode / name | `72379` / `13.1.1` | همان |
| minSdkVersion | `23` (اندروید ۶+) | همان |
| targetSdkVersion | `36` | همان |
| ABI | فقط `armeabi-v7a` | همان |
| کتابخانه‌های native | `libtmessages.49.so`, `liblanguage_id_l2c_jni.so` (ELF32/ARM) | `Verify APK is 32-bit ARM` |
| امضا | v1 ✅ v2 ✅ (apksigner exit 0) | `apksigner verify` |
| zip alignment | `Verification successful` | `zipalign -c -P 16` |
| extractNativeLibs | `true` | `aapt2 dump xmltree` |

با این وضعیت، APK از نظر ساختار، امضا و معماری سالم است؛ بنابراین علت باید در
سه دستهٔ باقی‌مانده باشد: **بستهٔ قبلی با امضای دیگر**، **نبود پشتیبانی ۳۲ بیتی روی
گوشی**، یا **فایل ناقص/نصب‌نشده از منبع**. کد `INSTALL_FAILED_*` دقیقاً یکی از این
سه را مشخص می‌کند.
