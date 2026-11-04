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

## ۷. ساخت APK با GitHub Actions (بدون نیاز به Android Studio روی سیستم شما)

workflow آماده است: `.github/workflows/build-apk.yml` (روی برنچ `arena/b6bc4d23-telegram`).

**اجرا (۲ کلیک):**
1. GitHub → تب **Actions** → workflow **«Build custom-backend APK»** → **Run workflow**
2. Branch را `arena/b6bc4d23-telegram` انتخاب کن، `backend_host` = IP/دامنه‌ی سرور ایران (یا `127.0.0.1` برای تست لوکال)، `backend_port` = `8443` → **Run**

نکته: آدرس بک‌اندJQ از فایل `server/deploy-host.txt` هم خوانده می‌شود — برای rebuild با IP جدید،
آن فایل را edit کن و push کن (push هم workflow را trigger می‌کند).

**خروجی build** (هر ۳ به‌صورت خودکار):
- Actions **artifact** به اسم `app-afat-release-apk`
- یک **GitHub Release** (pre-release با تگ `apk-custom-<sha>`) با فایل APK
- commit خودکار APK در مسیر **`releases/app-afat-release.apk`** همین برنچ (قابل دانلود با git)

## ۸. deploy سرور
`server/package_deploy.sh` → tar.gz را روی VPS ایران آپلود کن، با systemd یا
`nohup` اجرا کن — با این دستور (کلید dev تا وقتی تست است):

```bash
python3 -m mtserver.main --listen-host 0.0.0.0 --listen-port 8443 \
  --public-host <IP-یا-دامنه-سرور> --public-port 8443 \
  --dev-code 12345 --key server/dev_server_key.pem --http-status-port 8080
```

⚠️ سرور **باید با همان کلید** `server/dev_server_key.pem` اجرا شود که APK با آن build شده.
