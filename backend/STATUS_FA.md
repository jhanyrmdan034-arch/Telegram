# گزارش پیشرفت فنی — ۳ نوامبر ۲۰۲۶

## وضعیت فعلی

Backend مستقل REST/SSE در `backend/messenger-api/` پیاده شده است. اتصال لایهٔ دادهٔ Android نیز تا حد اصلی انجام شده و UI/نام برنامه تغییر نکرده است؛ بااین‌حال تا زمان موفق‌شدن build و آزمون emulator در CI، APK را قابل‌تحویل یا تأییدشده نمی‌دانیم.

## Android adapter

- `ConnectionsManager` در حالت `MESSENGER_API_ENABLED` درخواست‌های پشتیبانی‌شده را به `MessengerApiClient` می‌فرستد، پاسخ‌ها را روی `stageQueue` تحویل می‌دهد و لغو درخواست/GUID را مدیریت می‌کند؛ فعال‌شدن شبکهٔ native در این حالت مسدود شده است.
- ورود API، ذخیرهٔ Bearer رمز‌شده با Android Keystore، خروج/لغو نشست، جست‌وجوی کاربر، پروفایل، گفت‌وگوهای دونفره، history و پیام متنی به mappingهای TL متصل‌اند.
- دریافت زنده با SSE سطح حساب کار می‌کند تا پیام اول گفت‌وگوی تازه هم به دستگاه برسد؛ cursor رویداد در نشست محلی نگه داشته و پس از اتصال مجدد با `Last-Event-ID` replay می‌شود. SSE گفت‌وگوی باز نیز history را پس از اتصال تطبیق می‌دهد.
- `.github/workflows/messenger-api-android.yml` برای build debug، آزمون دو حساب از داخل Android instrumentation و launch smoke روی emulator اضافه شده است. Workflow هنوز اجرا نشده؛ workflow انتشار موجود دست‌نخورده مانده است.
- `TMessagesProj_AppTests/src/androidTest/java/org/telegram/messenger/MessengerApiClientIntegrationTest.java` مسیر Android client را برای ورود دو حساب، جست‌وجو، ساخت گفت‌وگو، ارسال/دریافت و دریافت SSE می‌آزماید؛ هنوز روی emulator اجرا نشده است.
- `TMessagesProj/build.gradle` مقدارهای `MESSENGER_API_ENABLED` و `MESSENGER_API_BASE_URL` می‌سازد؛ پیش‌فرض debug برابر `http://10.0.2.2:8080` است و برای build غیرمحلی باید property/environment روی HTTPS تنظیم شود.

## Backend و آزمون‌ها

Backend شامل ورود آزمایشی، نشست Bearer، پروفایل، جست‌وجوی کاربران، گفت‌وگوی دونفره، history، ارسال پیام، SSE گفت‌وگو و SSE سطح حساب، SQLite و seed است. جست‌وجوی کاربران بر نام، نام‌کاربری و شماره انجام می‌شود؛ شماره در خروجی عمومی نیست.

آخرین اجرا در `backend/messenger-api`: `node --check src/database.mjs`, `node --check src/server.mjs`, `npm test` — **۲ تست از ۲ تست موفق**. تست یکپارچه دو حساب، جست‌وجو، پیام، SSE گفت‌وگو، SSE حساب برای گفت‌وگوی تازه، replay رویداد و ماندگاری دیتابیس را بررسی می‌کند. `git diff --check` نیز اجرا شده است.

## محدودیت‌ها و گام بعدی

1. Push شاخه را انجام بده و workflow را اجرا کن؛ خطاهای Java/Gradle/NDK یا instrumentation را بر اساس CI اصلاح و تا سبزشدن build و emulator test ادامه بده.
2. در نبود JDK/Android SDK/ADB در محیط محلی، هیچ build یا تست Android محلی انجام نشده است.
3. ورود بدون OTP/رمز فقط برای نمونهٔ کنترل‌شده است و برای هاست عمومی ناامن و ممنوع؛ SQLite نیز فقط برای نمونهٔ تک‌فرآیندی است.
4. رسانه، گروه، رسید خواندن/تحویل، push، جریان کامل recovery/renew نشست و سرویس تولیدی هنوز خارج از محدوده‌اند. برای استفادهٔ بیرونی HTTPS، OTP، محدودسازی پایدار، PostgreSQL و سیاست عملیاتی لازم است.
