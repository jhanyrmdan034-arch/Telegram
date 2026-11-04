# راهنمای build و تست APK (versione test، بک‌اند自分)

## ۱. Fahm —什么是在发生
اپ اندروید همین ریپو (`TMessagesProj`) با UI کامل تلگرام ساخته می‌شود، فقط با
`#if CUSTOM_BACKEND` به سرور自分 (`server/mtserver`) وصل می‌شود به‌جای دیتاسنترهای تلگرام.

## ۲. پیش‌نیازهای build (روی سیستم توسعه‌دهنده)
- Android Studio 2025.1.4+، Android SDK 36، Android NDK 27.2.12479018 (طبق README ریپو)
- یک keystore برای sign کردن APK (Build > Generate Signed APK)

## ۳. اتصال اپ به سرور自分 (۲ دقیقه)
1. سرور را روی هاست اجرا کن (예: `server/run_demo.sh` یا Docker —_lookup `server/package_deploy.sh`)
2. در saída/log سرور یک بلاک print می‌شود با عنوان:
   `Paste this into TMessagesProj/jni/tgnet/CustomBackendConfig.h`
3. آن بلاک را **دقیقاً** در فایل `TMessagesProj/jni/tgnet/CustomBackendConfig.h` paste کن
   (مقدار `CUSTOM_BACKEND_HOST` را با IP/دامنه‌ی واقعی سرور ایران عوض کن، و `CUSTOM_BACKEND` روی `1` باشد).

## ۴. تنظیمات civis برای نسخه‌ی تست / نت ملی — `TMessagesProj/src/main/java/org/telegram/messenger/BuildVars.java`
```java
public static boolean CHECK_UPDATES = false;          // آپدیت از谷گل play — خاموش
public static boolean USE_CLOUD_STRINGS = false;      // stringها از سرور谷گل — خاموش (self-host)
public static boolean IS_BILLING_UNAVAILABLE = true;  // возможностей谷歌 billing — خاموش
public static boolean SUPPORTS_PASSKEYS = false;      // فقط روی api_id رسمی — خاموش
```

## ۵. Build
Android Studio → open project (`TMessagesProj`) → `Build > Generate Signed Bundle / APK` → APK
(نام پکیج را حتماً عوض کن، مثلاً `ir.example.chat` — طبق قوانین تلگرام حق استفاده از نام/لوگوی «Telegram» را نداری)

## ۶. نصب و تست
- `adb install app-debug.apk` یا فایل APK را مستقیم روی گوشی منتقل کن و نصب کن
  (برای نت ملی: APK مستقیم یا بازار/مایکت — Google Play در دسترس نیست)
- **ورود بدون کد (نسخه‌ی تست):** شماره любое (مثلاً `09120000000`) → کد любое (مثلاً `12345`) →
  سرور چون با `--dev-code` اجرا شده، هر کدی را قبول می‌کند و کاربر را می‌سازد.
- با دو گوشی (یا یک گوشی + تست‌کلاینت `server/test_client.py`) تست کن:
  پیام متنی، ارسال عکس، باز شدن تاریخچه، push زنده.

## ۷. FD — ordem deploy
`nokta`: `server/package_deploy.sh` → tar.gz را روی VPS ایران آپلود کن، با systemd یا
`nohup` اجرا کن، `--public-host` را IP/دامنه‌ی سرور بگذار.語 status روی پورت 8080.
