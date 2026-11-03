# گزارش تحلیل فنی سورس تلگرام اندروید — مأموریت ۱: شناخت کامل برای جایگزینی بک‌اند

> **وضعیت:** فقط تحلیل و مستندسازی — هیچ تغییری در سورس‌کد انجام نشده است.
> **نسخه سورس بررسی‌شده:** 13.1.1 (کد 7237) — API Layer 232
> **هدف نهایی:** پیام‌رسان مستقل با بک‌اند اختصاصی، با حفظ ۱۰۰٪ رابط کاربری و امکانات

---

## ۰. خلاصه مدیریتی

اپلیکیشن تلگرام اندروید از نظر معماری برای همین سناریو بسیار مناسب است، چون:

1. **تمام ارتباطات شبکه از یک دروازه واحد عبور می‌کند:** `ConnectionsManager.sendRequest*` (جاوا) ←→ موتور نیتیو `jni/tgnet` (C++). هیچ بخش دیگری از اپ مستقیماً با سرور حرف نمی‌زند (به‌جز چند سرویس جانبی مثل نقشه و پوش که جدا هستند).
2. **آدرس سرورها و کلید RSA در یک نقطه متمرکز است:** جدول دیتاسنترها در `jni/tgnet/ConnectionsManager.cpp` (حدود خط ۱۸۲۴) و کلیدهای عمومی RSA در `jni/tgnet/Handshake.cpp` (حدود خط ۳۶۷). یعنی «تغییر مسیر کلاینت به سرور ما» از نظر تئوری فقط همین دو نقطه + چند کلید API جانبی است.
3. **کلاینت، لیست دیتاسنتر را از خود سرور هم می‌پذیرد** (`help.getConfig` ←→ `onUpdateConfig` / `applyDatacenterAddress`). یعنی سرور ما می‌تواند کلاینت را به‌صورت داینامیک به DCهای خودمان هدایت کند.
4. **دیتابیس لوکال (SQLite) کامل و مستقل است:** ده‌ها جدول برای پیام‌ها، دیالوگ‌ها، مدیا، استیکر، سشن و… — منطق آفلاین/کش بدون تغییر باقی می‌ماند.
5. **رابط کاربری کاملاً از شبکه جداست:** همه صفحات (`org.telegram.ui`) فقط با کنترلرها و `NotificationCenter` (۳۴۸ ایونت داخلی) کار می‌کنند و مستقیماً به شبکه وصل نیستند.

**اما** حجم کار سمت سرور بسیار بزرگ است: اسکیمای API حدود **۲۰۸۸ کلاس** در `TLRPC.java` (۷۱۵۵۷ خط) + فایل‌های مکمل `tgnet/tl/*` (~۳۱ هزار خط) است. برآورد واقع‌بینانه: پیاده‌سازی کامل هم‌تراز، پروژه‌ای چندساله با تیم بک‌اند کامل است؛ ولی با اولویت‌بندی فازی (ابتدا احراز هویت + چت + فایل، بعد گروه/کانال، بعد تماس و…) می‌توان نسخه‌های کاربردی را مرحله‌به‌مرحله بیرون داد. جزئیات در بخش ۱۹ تا ۲۱.

### آمار کلیدی سورس (اندازه‌گیری‌شده از مخزن)

| مؤلفه | مسیر | اندازه |
|---|---|---|
| کل کد جاوا | `TMessagesProj/src/main/java` | ۲۲۳۰ فایل، ~۷۵ مگابایت |
| اسکیمای کامل MTProto API | `org/telegram/tgnet/TLRPC.java` | ۷۱٬۵۵۷ خط، ۲۰۸۸ کلاس |
| اسکیمای تفکیک‌شده جدید | `org/telegram/tgnet/tl/*.java` | ~۳۰٬۷۶۹ خط (۱۷ فایل) |
| نسخه API Layer | `TLRPC.java:76` | **Layer 232** |
| مغز پیام‌رسانی | `messenger/MessagesController.java` | ۲۵٬۶۴۱ خط |
| دیتابیس لوکال | `messenger/MessagesStorage.java` | ۱۹٬۰۶۴ خط |
| موتور شبکه نیتیو | `jni/tgnet/` | ~۸٬۴۰۰ خط C++‎ (چند فایل اصلی) |
| تماس صوتی/تصویری | `messenger/voip/VoIPService.java` | ۵٬۸۵۲ خط |
| استیکر/مدیادیتا | `messenger/MediaDataController.java` | ۱۰٬۳۴۸ خط |
| استوری | `ui/Stories/StoriesController.java` | ۵٬۲۶۷ خط |
| اعلان‌ها | `messenger/NotificationsController.java` | ۶٬۴۸۱ خط |
| پوش | `messenger/PushListenerController.java` | ۱٬۸۵۴ خط |
| مخاطبین | `messenger/ContactsController.java` | ۳٬۱۲۹ خط |
| آپلود/دانلود فایل | `messenger/FileLoader.java` | ۱٬۹۷۴ خط |
| گذرگاه ارتباطات داخلی | `messenger/NotificationCenter.java` | **۳۴۸ ایونت** |
| حداکثر اکانت هم‌زمان | `UserConfig.MAX_ACCOUNT_COUNT` | ۴ اکانت |

---

## ۱. معماری فعلی اپلیکیشن

### ۱.۱. ماژول‌های گریدل

| ماژول | نقش |
|---|---|
| `TMessagesProj` (library) | ~۹۹٪ کد: کل هسته، شبکه، دیتابیس و UI |
| `TMessagesProj_App` | نسخه اصلی گوگل‌پلی (`applicationId = APP_PACKAGE`) + مانیفست‌ها |
| `TMessagesProj_AppHuawei` | نسخه HMS (بدون سرویس گوگل، پوش هواوی) |
| `TMessagesProj_AppStandalone` | نسخه مستقیم سایت (پسوند `.web`) با پرداخت فاکتوری |
| `TMessagesProj_AppHockeyApp` | نسخه بتای داخلی (HA_private/public/hardcore) |
| `TMessagesProj_AppTests` | تست‌ها |
| `TMessagesProj_Modules/media` (ساب‌ماژول) | ExoPlayer/Media3 فورک تلگرام |
| `TMessagesProj/lib/jlatexmath` (ساب‌ماژول) | رندر فرمول LaTeX در مقالات |

### ۱.۲. لایه‌های معماری (از پایین به بالا)

```
┌─────────────────────────────────────────────────────────┐
│ لایه ۵: رابط کاربری  org.telegram.ui (اکتیویتی/فرگمنت/ویو) │
│   - هیچ دسترسی مستقیمی به شبکه ندارد                      │
├─────────────────────────────────────────────────────────┤
│ لایه ۴: کنترلرهای سرویس  org.telegram.messenger          │
│   - MessagesController، ContactsController، ...          │
│   - ارتباط با UI فقط از طریق NotificationCenter         │
├─────────────────────────────────────────────────────────┤
│ لایه ۳: دروازه API جاوا  org.telegram.tgnet              │
│   - ConnectionsManager.sendRequest* (تنها نقطه خروج)    │
│   - TLRPC + TL_* : کلاس‌های سریالایز اسکیما              │
├─────────────────────────────────────────────────────────┤
│ لایه ۲: موتور شبکه نیتیو  jni/tgnet (C++‎)                │
│   - سوکت، پروکسی، رمزنگاری، handshake، سشن، DC           │
├─────────────────────────────────────────────────────────┤
│ لایه ۱: ذخیره‌سازی لوکال                                 │
│   - MessagesStorage (SQLite) + SharedPreferences        │
│   - FileLoader (فایل‌های دانلودی/آپلودی روی دیسک)        │
└─────────────────────────────────────────────────────────┘
```

**نکته استراتژیک:** برای جایگزینی بک‌اند، «قرارداد» ما با کلاینت = همان متدهای MTProto در Layer 232 + رفتارهای transport (که در بخش ۳ آمده). تا وقتی سرور ما همین قرارداد را رعایت کند، لایه‌های ۴ و ۵ و ۱ **بدون هیچ تغییری** کار می‌کنند.

### ۱.۳. مولتی‌اکانت

- همه کنترلرها per-account هستند: `XxxController.getInstance(account)` و `ConnectionsManager.getInstance(account)` و `UserConfig.getInstance(account)`.
- `AccountInstance.java` نگهدارنده ارجاع‌های یک اکانت است. حداکثر ۴ اکانت.
- **پیامد برای بک‌اند ما:** سرور باید چند auth_key هم‌زمان برای یک دستگاه را پشتیبانی کند (طبیعی است؛ هر اکانت یک سشن/کلید جدا دارد).

---

## ۲. نقشه ارتباطات داخلی

### ۲.۱. مسیر یک درخواست API (رفت)

```
UI (مثلاً ChatActivity)
  → SendMessagesHelper / MessagesController (ساخت آبجکت TL_messages_sendMessage)
  → ConnectionsManager.sendRequest(object, delegate, flags, connectionType, datacenterId)
  → JNI: TgNetWrapper → native sendRequest
  → Datacenter/ConnectionSession: ساخت msg_id/msg_seqno، فشرده‌سازی gzip، رمز AES-IGE
  → ConnectionSocket: (اختیاری: پروکسی SOCKS5/MTProto) → TCP (+obfuscation) → سرور
```

### ۲.۲. مسیر پاسخ و آپدیت (برگشت)

```
سرور → ConnectionSocket → Datacenter (رمزگشایی، اعتبارسنجی msg_id، ack خودکار)
  → onRequestComplete(requestToken, response...) → RequestDelegate در جاوا
  → MessagesController.processUpdates / processUpdateArray
  → MessagesStorage.putMessages (ذخیره SQLite)
  → NotificationCenter.postNotificationName(UPDATE_*, ...)  ← ۳۴۸ ایونت
  → UIها که observe کرده‌اند خودشان را refresh می‌کنند
```

آپدیت‌های لحظه‌ای (بدون درخواست) هم از همین مسیر می‌آیند: نیتیو ← `ConnectionsManager.onUpdate(account)` ← دریافت اختلاف (`updates.getDifference`) ← پردازش و ذخیره ← ایونت داخلی.

### ۲.۳. نخ‌ها و صف‌ها

- `DispatchQueue` / `DispatchQueuePoolBackground`: صف‌های پس‌زمینه (شبکه، دیتابیس، مدیا).
- دیتابیس فقط روی storageQueue لمس می‌شود؛ UI فقط روی main thread.
- نیتیو چند connection موازی در هر DC باز می‌کند (generic/download/upload/push/temp — بخش ۳).

---

## ۳. پروتکل MTProto در این کلاینت (قلب تپنده اتصال)

### ۳.۱. فایل‌ها و کلاس‌ها

| بخش | فایل | نقش |
|---|---|---|
| دروازه جاوا | `tgnet/ConnectionsManager.java` | `sendRequest*`، `cancelRequest`، `init`، `applyDatacenterAddress`، `switchBackend`، کال‌بک‌های `onRequestComplete/onUpdate/onConnectionStateChanged/onUpdateConfig` |
| سریالایز | `tgnet/TLObject/TLMethod/SerializedData/Input/Output/NativeByteBuffer` | خواندن/نوشتن باینری TL |
| رجیستری تایپ‌ها | `tgnet/TLClassStore.java` | ساخت آبجکت از روی constructor-id |
| اسکیما | `tgnet/TLRPC.java` + `tgnet/tl/*.java` | همه تایپ‌ها و متدهای Layer 232 |
| ارکستر نیتیو | `jni/tgnet/ConnectionsManager.cpp` (~۴۰۰۰ خط) | مدیریت DCها، صف درخواست، retry، مهاجرت DC |
| سشن/DC | `jni/tgnet/Datacenter.cpp` (۱۵۸۵ خط) + `Connection*.cpp` | رمزنگاری، salt، msg_id/seqno، ack، container، reconnect |
| handshake | `jni/tgnet/Handshake.cpp` (۱۰۹۷ خط) | ساخت auth_key با RSA+DH |
| سوکت/پروکسی | `jni/tgnet/ConnectionSocket.cpp` (۷۵۸ خط) | TCP، obfuscation، SOCKS5 و MTProto Proxy |
| رمز IGE | `jni/openssl/aes_ige.c` + boringssl | AES-256-IGE (فراخوانی در `Datacenter.cpp:989-992`) |
| پیکربندی DC | `jni/tgnet/Config.cpp/h` | ذخیره/بازیابی آدرس DC و کلیدها |

### ۳.۲. آدرس‌های هاردکدشده (نقطه اتصال به تلگرام)

در `jni/tgnet/ConnectionsManager.cpp` حدود خط ۱۸۲۴، جدول پیش‌فرض دیتاسنترها:

```
149.154.175.50 / 149.154.167.51 / 149.154.175.100 / 149.154.167.91 /
149.154.171.5 / 149.154.175.40 / 149.154.167.40 / 149.154.175.117  (پورت 443)
```

- `switchBackend()` / `isTestBackend()` (`ConnectionsManager.cpp:594,2046`): حالت تست (DCهای آزمایشی تلگرام) — الگوی آماده برای «تعویض بک‌اند» اما هنوز به زیرساخت تلگرام وصل است.
- **مهم:** کلاینت علاوه بر این جدول، لیست DC را از پاسخ `help.getConfig` هم می‌گیرد و با `applyDatacenterAddress` اعمال می‌کند (`onUpdateConfig` در `ConnectionsManager.java:944`). یعنی سرور ما می‌تواند DCهای خودش را به کلاینت معرفی/مهاجرت دهد.

### ۳.۳. handshake و رمزنگاری (باید در سرور ما بازپیاده‌سازی شود)

1. `req_pq` → `req_DH_params` → `set_client_DH_params` (کلاسیک MTProto 2.0).
2. کلیدهای عمومی RSA سرور **داخل کلاینت امبد شده** (`Handshake.cpp` ~خط ۳۶۷ به بعد، دو کلید PEM). چون کلید خصوصی تلگرام را نداریم، **باید کلید RSA خودمان را در همین‌جا جایگزین کنیم** (تغییر کوچک نیتیو + ری‌بیلد `.so` — پایپ‌لاین بیلدش را داریم).
3. پیام‌ها: `auth_key_id + msg_key + AES-256-IGE(auth_key, msg_key)`؛ اعتبارسنجی `msg_id` (تازه‌بودن/یکتایی) و `seqno`؛ `server_salt` دوره‌ای (`get_future_salts`)؛ ack خودکار؛ `msg_container` برای بچ؛ `gzip_packed` برای فشرده‌سازی؛ `msgs_ack`، `bad_msg_notification`، `new_session_created`.
4. `initConnection` با پارامترها: `api_id` (فعلاً ۴)، مدل دستگاه، نسخه سیستم/اپ، زبان، و `app_version`. سرور ما می‌تواند `api_id` دلخواه خودش را تعریف کند (توصیه: عوض شود).

### ۳.۴. انواع کانکشن

`ConnectionsManager.java:88` به بعد: `Generic` (متدهای معمول)، `Download`/`Download2` (دانلود فایل از DC فایل)، `Upload`، `Push` (کانکشن دائمی کم‌مصرف برای آپدیت)، `Temp` (موقت، مثلاً CDN/کارهای خاص). سرور ما باید همه را روی همان پورت/پروتکل جواب بدهد (تفاوت فقط منطقی است).

### ۳.۵. مهاجرت بین DC و خطاها (قرارداد اجباری سرور)

- کلاینت خطاهای `NETWORK_MIGRATE_X` / `PHONE_MIGRATE_X` / `USER_MIGRATE_X` را در **نیتیو** هندل و خودکار به DC جدید وصل می‌شود (`ConnectionsManager.cpp:1256`). `FILE_MIGRATE_X` در مسیر دانلود فایل.
- `FLOOD_WAIT_X` در ده‌ها نقطه UI/کنترلر هندل می‌شود (مثلاً `AlertsCreator.java`) — سرور ما باید ریت‌لیمیت استاندارد با همین فرمت برگرداند وگرنه رفتار کلاینت غیرقابل‌پیش‌بینی می‌شود.
- آپدیت‌ها با مدل **pts/qts/seq** مرتب می‌شوند و روی حفره، `updates.getDifference` / `getChannelDifference` صدا زده می‌شود (جدول `messages_seq`). **پیاده‌سازی دقیق سمانتیک difference حیاتی‌ترین بخش سازگاری است.**

---

## ۴. ورود و ثبت‌نام با شماره تلفن + کد تأیید + 2FA + نشست‌ها

### فایل‌ها

| بخش | فایل |
|---|---|
| کل فلو UI ورود | `ui/LoginActivity.java` (بزرگ؛ همه استپ‌ها: intro، شماره، کد، رمز، ثبت‌نام، QR) |
| اینترو/آنبوردینگ | `ui/IntroActivity.java`، `ActionIntroActivity.java` |
| وضعیت احراز هویت | `messenger/UserConfig.java` (`isClientActivated()`، `currentUser`، `loginTime`، `savedPasswordHash/Time`، `registeredForPush`) |
| توکن‌های احراز | `messenger/AuthTokensHelper.java` |
| احرازهای تأییدنشده | `messenger/UnconfirmedAuthController.java` |
| مدیریت نشست‌ها | `ui/SessionsActivity.java` |
| QR لاگین (اسکن/نمایش) | `ui/LaunchActivity.java` + `SessionsActivity.java` (`LoginToken`) |
| رمز دو مرحله‌ای (SRP) | داخل `LoginActivity.java` + متدهای `account.getPassword / updatePasswordSettings` |
| پس‌کی | `messenger/PasskeysController.java` (فقط روی اپ‌آیدی رسمی کار می‌کند) |

### متدهای API درگیر (اسکیما)

`auth.sendCode` → (کد از کانال‌های: `auth.codeTypeSms/Call/FlashCall/MissedCall/FragmentSms` — همه در `TLRPC.java`) → `auth.signIn` یا `auth.signUp` → (اختیاری) `auth.checkPassword` / `auth.checkRecoveryPassword` → `auth.cancelCode`، `auth.resendCode`، `auth.exportLoginToken`/`acceptLoginToken` (QR)، `account.getAuthorizations`/`resetAuthorization` (نشست‌ها)، `account.updatePasswordSettings` (2FA)، `account.confirmPasswordEmail`/`verifyEmail` و…

### چگونه کار می‌کند

1. `sendCode` شماره را می‌فرستد؛ سرور کد را از طریق پیامک/تماس/فلش‌کال/اپ می‌فرستد و `phone_code_hash` برمی‌گرداند.
2. کاربر کد را وارد می‌کند؛ `signIn` با `(phone, phone_code_hash, code)` سشن می‌سازد یا `SESSION_PASSWORD_NEEDED` می‌دهد (رفتن به 2FA با SRP) یا `PHONE_NUMBER_UNOCCUPIED` (رفتن به ثبت‌نام با نام/نام‌خانوادگی).
3. پس از موفقیت، `currentUser` ذخیره و `isClientActivated=true` می‌شود؛ بعد `registerDevice` برای پوش و `getAppConfig`/`getConfig` برای پیکربندی.
4. سکرت‌چت‌ها روی دستگاه جدید منتقل نمی‌شوند (E2E) — رفتار استاندارد حفظ می‌شود.

### تغییرات لازم برای بک‌اند ما

- **درگاه پیامک/صوتی واقعی** برای ارسال کد (یا حالت توسعه با کد ثابت/نمایش در پنل). همه `codeType*`ها را لازم نیست روز اول داشته باشیم؛ SMS + (اختیاری) تماس کافی است و UI همان می‌ماند (سرور نوع را اعلام می‌کند).
- پیاده‌سازی کامل: `sendCode/signIn/signUp/checkPassword/resendCode/cancelCode` + خطاهای استاندارد (`PHONE_CODE_INVALID/EXPIRED/EMPTY`، `SESSION_PASSWORD_NEEDED`، `PHONE_NUMBER_UNOCCUPIED/BANNED/FLOOD`، `2FA_RECENT_CONFIRM` و…).
- **SRP دو مرحله‌ای** باید دقیقاً با همان پارامترها (`srp_id/srp_B/secure_random`, SHA256) پیاده شود وگرنه لاگین کاربران 2FA می‌شکند.
- QR لاگین: `exportLoginToken/acceptLoginToken` + آپدیت `updateLoginToken`؛ وگرنه دکمه QR بی‌اثر می‌شود.
- نشست‌ها: ذخیره device/platform/app_version برای هر auth_key + امکان خاتمه تکی/همه.

### چه چیزی بدون تغییر می‌ماند

کل UI ورود/ثبت‌نام، ولیدیشن شماره (`PhoneFormat/`)، صفحه 2FA، صفحات نشست‌ها، مولتی‌اکانت، انیمیشن‌ها.

### وابستگی به تلگرام

شماره‌ها/هویت‌ها امروز روی زیرساخت تلگرام‌اند؛ کل فضای هویت (user_idها، شماره‌ها) در مهاجرت باید از صفر روی سرور ما ساخته شود (امکان «انتقال اکانت تلگرامی» وجود ندارد).

---

## ۵. مدیریت کاربران و حساب‌ها و مخاطبین

### فایل‌ها

- `messenger/ContactsController.java` (۳۱۲۹ خط): دفترچه، ایمپورت/سینک، حذف، استتوس‌ها.
- `messenger/UserObject.java` / `DialogObject.java` / `ChatObject.java`: هلپرهای موجودیت.
- `messenger/UserNameResolver.java`: `contacts.resolveUsername` + کش + لینک‌های t.me.
- `messenger/ContactsSyncAdapterService.java` + `AuthenticatorService.java`: سینک با اکانت اندروید.
- `messenger/BirthdayController.java`، پروفایل‌ها: `ui/ProfileActivity.java`، `ChangeName/Bio/UsernameActivity`.

### متدهای API

`contacts.getContacts/getStatuses/importContacts/deleteContacts/addContact/resetSaved`، `contacts.resolveUsername`، `users.getUsers/getFullUser`، `account.updateProfile/updateUsername/updateBirthday`، `photos.*` (عکس پروفایل)، `contacts.block/unblock/getBlocked`، `account.getPrivacy/setPrivacy`، `account.updateStatus` (آنلاین/آفلاین).

### تغییرات لازم

- گراف مخاطبین + سرچ سراسری یوزرنیم + بلاک/پرایوسی + استتوس آنلاین (با قواعد حریم‌خصوصی) روی سرور ما.
- **لینک‌های t.me**: امروز با `resolveUsername` روی بک‌اند تلگرام ریزالو می‌شوند. برای ما دو راه است: (الف) نگه‌داشتن فرمت `t.me/xxx` ولی ریزالو از بک‌اند خودمان (تغییر کوچک کلاینت در هندل دیپ‌لینک، بدون تغییر ظاهری)، (ب) دامین اختصاصی خودمان. پیشنهاد: (الف) برای سازگاری ذهنی کاربر.
- ایمپورت مخاطبین حجیم (هش شماره‌ها) باید امن و ریت‌لیمیت‌شده باشد.

### بدون تغییر

دفترچه تلفن، sync adapter، UI پروفایل/مخاطبین، کش‌ها و جداول (`contacts`، `chat_hints` و…).

---

## ۶. ارسال، دریافت و ذخیره پیام‌ها (+ دیتابیس)

### فایل‌ها

| بخش | فایل |
|---|---|
| ارسال/صف/زمان‌بندی/ریترای | `messenger/SendMessagesHelper.java` (چندهزار خط؛ `performSendMessageRequest*`، پیام‌های تأخیری/زمان‌دار) |
| دریافت/دیالوگ/کش | `messenger/MessagesController.java` (۲۵٬۶۴۱ خط؛ `processUpdates/processUpdateArray`، `getDifference`، dialogs) |
| چت مخفی E2E | `messenger/SecretChatHelper.java` (رمز/رمزگشایی، مدیریت کلید، TTL، اسکرین‌شات) |
| دیتابیس | `messenger/MessagesStorage.java` (۱۹٬۰۶۴ خط) |
| UI چت | `ui/ChatActivity.java` (+ صدها کامپوننت) |

### متدهای API (نمونه پرکاربرد)

`messages.sendMessage/sendMedia/sendMultiMedia`، `messages.getHistory/getDialogs/search`، `messages.getMessagesViews/readHistory`، `messages.editMessage/deleteMessages`، `messages.sendReaction/getReactions`، `messages.forwardMessages`، `updates.getDifference/getChannelDifference/getState`، `messages.getPinnedDialogs/saveDraft`، `messages.getScheduledHistory/sendScheduled`، `messages.requestEncryption/acceptEncryption/sendEncrypted*` (سکرت‌چت).

### جداول SQLite (گزیده از `MessagesStorage.java`)

`dialogs`، `messages_v2`، `messages_topics`، `messages_holes(_topics)`، `media_v2(_topics)` + شمارنده‌ها/حفره‌ها، `chats`، `enc_chats` + `enc_tasks_v4`، `polls_v2` + رأی‌ها، `bot_keyboard(_topics)`، `dialog_filter*` (پوشه‌ها)، `chat_pinned_v2`، `chat_settings_v2`، `quick_replies_messages`، `business_*`، `fact_checks`، `profile_stories*`، `download_queue`، `keyvalue`، `params`، `messages_seq` (وضعیت pts/qts/seq) و ده‌ها جدول دیگر.

### چگونه کار می‌کند

- ارسال خوش‌بینانه (optimistic): پیام فوراً در UI/DB با حالت «در حال ارسال» می‌نشیند؛ `random_id` یکتا جلوی تکرار می‌گیرد؛ پاسخ سرور (id/date/pts) جایگزین می‌شود؛ quick-ack برای حس سرعت.
- دریافت: آپدیت زنده یا `getDifference` روی حفره/شروع؛ ترتیب‌بندی با pts (شخصی/گروه کوچک) و qts/seq؛ مسیج‌های کانال با pts خود کانال + `getChannelDifference`.
- همه‌چیز در SQLite کش می‌شود؛ اپ آفلاین هم کامل قابل مرور است.

### تغییرات لازم

- این سنگین‌ترین بخش سرور است: ذخیره پیام، ordering سراسری، difference دقیق، سرچ، فوروارد، ادیت/حذف دوطرفه، درفت، زمان‌بندی، پین، ری‌اکشن، ویو، فولدرها، کوت/ریپلای/منشن، هشتگ، نظرسنجی، business/quick-reply، fact-check (اختیاری/مرحله‌ای).
- **سکرت‌چت:** سرور فقط رله‌کننده opaque است (ذخیره موقت برای تحویل آفلاین + صف). رمزنگاری سرتاسری سمت کلاینت می‌ماند (`SecretChatHelper` + boringssl + ‎`libtde2e/libtdutils` نیتیو). پس «زیرساخت اختصاصی» با E2E ناسازگار نیست.
- ریت‌لیمیت + `FLOOD_WAIT` استاندارد + `MESSAGE_ID_INVALID` و خطاهای مرزی مشابه.

### بدون تغییر

کل UI چت، کل دیتابیس و منطق کش/حفره، صف ارسال، E2E.

---

## ۷. گروه‌ها و کانال‌ها (+ تاپیک/انجمن)

### فایل‌ها و API

- API: فضای `channels.*` (ساخت/ویرایش/ادمین/بن/پارتیسیپنت/لینک دعوت/لاگ ادمین/`convertToGigagroup`/کامنت/username/…) — ده‌ها متد در `TLRPC.java`؛ `messages.*` برای پایه؛ `TL_forum`/`TopicsController.java` برای تاپیک‌ها؛ `MemberRequestsController.java` برای درخواست‌های عضویت؛ `ChatObject.java` هلپرها.
- UI: `ChannelCreateActivity`، `ChatEdit/ChatUsers/ChatRights/ChannelAdminLog/TopicsFragment` و…

### تغییرات لازم

- مدل دسترسی چندسطحی (creator/admin/rights/banned/defaultBannedRights)، لینک‌های دعوت (هش یکتا + revoke)، کانتر اعضا/ویو، slowmode، امضا/پروفایل بحث (discussion linkage)، لاگ ادمین، گیگاگروپ/برادکست، آنتی‌اسپم پایه، `checkUsername` سراسری برای لینک عمومی.
- تاپیک‌ها/انجمن‌ها: `messages_topics` + API تاپیک (ساخت/بستن/پین/آیکون/نوتیف جدا).

### بدون تغییر

همه UIهای مدیریت/عضویت/لاگ/تاپیک؛ جداول کش (`channel_users_v2`، `channel_admins_v3` و…).

---

## ۸. تصاویر، ویدئو، فایل، ویس (+ CDN)

### فایل‌ها

- `messenger/FileLoader.java` (ارکستر)، `FileUploadOperation.java` (`upload.saveFilePart/saveBigFilePart`، چانک ۵۱۲KB/۱MB، big-file برای >۱۰MB)، `FileLoadOperation.java` (`upload.getFile` + **CDN**: `upload.getCdnFile/getCdnFileHashes/reuploadCdnFile` + `upload.fileCdnRedirect`)، `FileStreamLoadOperation` (استریم)، `DownloadController` + `download_queue`، `FileRefController` (تازه‌سازی `file_reference` منقضی)، `FileLoaderPriorityQueue`، `DownloadRequestPacer`.
- مدیاپلیر: ExoPlayer فورک (`TMessagesProj_Modules/media`)؛ ویس: `MediaController` + `AudioRecorderV2`؛ گیف/استیکر متحرک: tlottie نیتیو.

### مدل آدرس‌دهی فایل (مهم برای سرور ما)

هر فایل = `(dc_id, id, access_hash, file_reference)` + (برای عکس‌ها) سایزها/thumbها. `file_reference` ممکن است منقضی شود و کلاینت با `FileRefController` از مرجع (پیام/چت/استیکرست) تازه‌اش می‌کند.

### تغییرات لازم (خبر خوب: ساده‌سازی‌های مجاز)

- پیاده‌سازی `saveFilePart/saveBigFilePart/getFile` + صدور `access_hash/file_reference` روی آبجکت‌استوریج خودمان (S3-compatible + CDN جلویی).
- **CDN تلگرام را لازم نداریم:** اگر سرور ما هیچ‌وقت `fileCdnRedirect` برنگرداند، مسیر CDN کلاینت هرگز فعال نمی‌شود و همان کد بدون تغییر کار می‌کند.
- می‌توانیم `file_reference` را «هرگز منقضی‌شونده» صادر کنیم؛ منطق refresh دست‌نخورده می‌ماند ولی صدا زده نمی‌شود.
- اگر همه فایل‌ها روی همان DC اکانت باشند، خطای `FILE_MIGRATE_X` هم پیش نمی‌آید.
- thumb/سایز عکس/ویدئو: یا سمت کلاینت ساخته می‌شود (امروز همین است) یا سرور؛ قرارداد `photoSize/documentAttributeVideo` باید دقیق باشد.
- ویس/ویدئومسیج/استریم/دانلود خودکار: همه روی همین زیرساخت می‌نشینند.

### بدون تغییر

کل پایپ‌لاین آپلود/دانلود/استریم/کش دیسک، پلیرها، وویس، گالری.

---

## ۹. اعلان‌ها و Push Notification

### فایل‌ها

- `messenger/NotificationsController.java` (۶٬۴۸۱ خط): ساخت/مدیریت نوتیف، کانال‌ها، ریپلای مستقیم، وearable، ویجت‌چت‌ها.
- `messenger/PushListenerController.java` (۱٬۸۵۴ خط): گرفتن توکن **FCM** (`PUSH_TYPE_FIREBASE=2`) و **HMS هواوی** (`PUSH_TYPE_HUAWEI=13`) + اتصال پوش MTProto.
- `messenger/GcmPushListenerService.java` (۴۳ خط): entrypoint پیام FCM.
- `NotificationRepeat`/`NotificationCallbackReceiver`/`PopupReplyReceiver`/`WearReplyReceiver`/`AppStartReceiver` (بیدار شدن پس از بوت).
- ثبت دستگاه: `MessagesController.java:16198` (`account.registerDevice`) و `:16114` (`unregisterDevice`)؛ `setPushConnectionEnabled` برای کانکشن پوش.

### تغییرات لازم

- **پروژه Firebase خودمان** + `google-services.json` جدید (تغییر روتین کلاینت) و/یا HMS برای هواوی؛ سرور ما endpoint ثبت توکن (`registerDevice`) را پیاده و صف ارسال FCM/HMS را خودش مدیریت می‌کند.
- فرمت payload پوش باید همان قرارداد فعلی باشد (کلاینت از روی آن فقط «بیدار شو و sync بگیر» را می‌فهمد + پیش‌نمایش امن).
- کانکشن `Push` از همان DC ما باز می‌شود (خودکار).
- نوتیف تماس ورودی (CallReceiver + فول‌اسکرین اینتنت) روی همین مسیر می‌نشیند.

### بدون تغییر

همه منطق نوتیف/ریپلای/ویجت/وِر؛ فقط فرستنده پوش عوض می‌شود.

---

## ۱۰. تماس صوتی و تصویری (۱به۱ و گروهی)

### فایل‌ها

| بخش | فایل |
|---|---|
| سرویس تماس ۱به۱ | `messenger/voip/VoIPService.java` (۵٬۸۵۲ خط) + `VoIPServiceState`، `NativeInstance`، `Instance`، `TelegramConnectionService`، `VoipAudioManager`، `VideoCapturerDevice` |
| تماس گروهی/لایو | `voip/ConferenceCall.java`، `GroupCallMessagesController.java`، `ui/GroupCallActivity.java`، `ui/Stories/LivePlayer.java` |
| سیگنالینگ | `tgnet/tl/TL_phone.java` (۱٬۵۵۴ خط؛ `phone.requestCall/acceptCall/confirmCall/receivedCall/discardCall/getCallConfig` + `create/join/leave/discardGroupCall` و…) |
| چسب UI | `ui/Components/voip/VoIPHelper.java` (۸۲۶ خط)، `ChatActivity` |
| مدیا انجین | ساب‌ماژول `tgcalls` + `voip/webrtc` داخل‌مخزنی + `absl/libyuv` (کامپایل از سورس)؛ `libyuv/absl` و… |
| اشکال‌زدایی تماس | `phone.saveCallDebug` + `VoIPDebugToSend` |

### چگونه کار می‌کند

- **۱به۱:** سیگنالینگ روی MTProto (تبادل `g_a/g_b` و fingerprint کلید + کاندیداهای اتصال)؛ مدیا ترجیحاً **P2P (SRTP)** و در صورت نیاز از **reflector/relay تلگرام** (آدرس‌ها در `phoneCallProtocol`/`getCallConfig` می‌آیند).E2E با اموجی‌های تأیید (`EncryptionKeyEmojifier`).
- **گروهی/ویدیوچت/لایو:** مدیا از **SFU/سرورهای تلگرام** می‌گذرد (`groupCall` + `getGroupCallStreamChannels` + پیام‌های سینگال گروهی). یعنی بدون سرور مدیای سازگار، تماس گروهی کار نمی‌کند.

### تغییرات لازم (یکی از سخت‌ترین بخش‌ها)

- سیگنالینگ `phone.*`: قابلیت پیاده‌سازی متوسط (CRUD تماس + روتینگ پیام به طرف مقابل + `getCallConfig` خودمان).
- **مدیا ۱به۱:** اگر فقط P2P بخواهیم، پشت NATهای ایران/اپراتورها زیاد fail می‌شود؛ عملاً به **reflector/TURN خودمان** نیاز داریم (سرور سبک رله UDP).
- **تماس گروهی:** نیازمند **SFU سازگار با پروتکل tgcalls** (ترکیب/فوروارد SRTP + SSRCها + رمز گروهی). پروتکل سمت‌سرور تلگرام اختصاصی و مستندنشده است؛ عملاً باید از روی رفتار کلاینت مهندسی معکوس + تست شود، یا در فاز اول تماس گروهی را با همان UI اما پیاده‌سازی جایگزین (مثلاً SFU استاندارد + آداپتر سیگنالینگ در کلاینت، بدون تغییر ظاهری) ارائه کرد. این بزرگ‌ترین ریسک فنی پروژه است.
- پوش تماس ورودی + CallKit/ConnectionService بدون تغییر می‌مانند.

### بدون تغییر

کل UI تماس/گروه‌کال/لایو، E2E تماس ۱به۱، انجین صوتی/تصویری کلاینت.

---

## ۱۱. استیکر، ایموجی و Premium

### فایل‌ها و API

- `messenger/MediaDataController.java` (۱۰٬۳۴۸ خط): نصب/حذف/علاقه‌مندی/اخیر/فیچرد/ماسک/ایموجی/ایموجی‌استتوس/کیوردها/پک‌ها.
- API: `messages.getStickers/getAllStickers/getFeaturedStickers/getRecentStickers/getFavedStickers/getStickerSet/getMaskStickers/getEmojiStickers/...` (+ ایموجی‌استتوس‌ها، `account.getEmojiStatuses` و…)
- رندر: tlottie نیتیو (استیکر متحرک) + `AnimatedEmoji`/`CompoundEmoji`.
- Premium: فلگ `user.premium` + کامپوننت‌های `ui/Components/Premium/*` + `BillingController.java` (Google Play Billing 8 + fallback فاکتوری) + جدول `premium_promo` + `payments.canPurchaseStore`.

### تغییرات لازم

- **کتابخانه استیکر/ایموجی خودمان:** هاست + API نصب/جستجو/فیچرد. می‌توان ست‌های پایه را سید کرد؛ کاربران هم می‌توانند ست بسازند (نیازمند ربات/پنل معادل @Stickers — یا API مستقیم ساخت ست).
- **Premium:** مدل اشتراک مال ماست: entitlement روی سرور ما + درگاه پرداخت خودمان (Play Billing با محصول خودمان در کنسول + invoice). گیت‌های UI (`PremiumLock...`) بر اساس فلگ سرور کار می‌کنند و دست‌نخورده می‌مانند.

### بدون تغییر

همه UI استیکر/ایموجی/پرمیوم، رندرها، کش‌ها.

### وابستگی خاص

استیکرهای «پرمیوم/گیفت تلگرام» و ایموجی‌استتوس‌های رسمی قابل انتقال نیستند (مالکیت تلگرام)؛ باید کاتالوگ خودمان را بسازیم یا لایسنس بگیریم.

---

## ۱۲. استوری

### فایل‌ها و API

- `ui/Stories/StoriesController.java` (۵٬۲۶۷ خط) + ویوها (`PeerStoriesView`، `ProfileStoriesView`، `SelectStoriesBottomSheet` و…) + `TL_stories.java` (۳٬۳۳۰ خط): `stories.getAllStories/getPeerStories/getPinnedStories/getStoriesArchive/getStoriesByID/searchPosts`، `stories.sendStory/editStory/deleteStories`، `stories.readStories/incrementStoryViews/sendReaction`، `stories.getChatsToSend` (فوروارد/ری‌پست)، آلبوم‌ها (`getAlbumStories`)، کالکشن‌های پروفایل.
- جداول: `profile_stories*`، `dialog_photos*` (کاورها).

### تغییرات لازم

- ذخیره استوری (مدیا + کپشن + پرایوسی + انقضا ۲۴ساعته/پین) + فید `getAllStories` + سین/ویو + ری‌اکشن + آرشیو + آلبوم + فوروارد. منطق انقضا/پرایوسی سمت سرور.
- مدیا روی همان آبجکت‌استوریج بخش ۸.

### بدون تغییر

کل UI استوری/آرشیو/آلبوم/ری‌اکشن.

---

## ۱۳. ربات‌ها (+ WebApp + Bot API)

### فایل‌ها و API

- کیبورد/دکمه: `TL_keyboard.java` (۱٬۸۷۷ خط)، جداول `bot_keyboard(_topics)`، `BotInlineKeyboard`، `BotForumHelper`، `BotFullscreenButtons`.
- وب‌اپ‌ها: `ui/bots/*` (`BotWebViewSheet/AttachedSheet/MenuContainer` + `WebviewActivity`)، جدول `attach_menu_bots`، `TL_bots.java` (۱٬۳۳۰ خط).
- کش/دیسکاوری: `botcache`، `popular_bots`.
- چت‌بات‌ها از دید کلاینت = یوزرهای `bot=true` + پیام‌های دارای `reply_markup`.

### تغییرات لازم (دو لایه)

1. **سمت کلاینت‌نما (MTProto):** همان متدهای پیام/کیبورد/کال‌بک/اینلاین که ربات‌ها مصرف می‌کنند — با پیاده‌سازی بخش ۶ پوشش داده می‌شود.
2. **Bot API سازگار با `api.telegram.org` (سرویس HTTP جدا):** اگر می‌خواهیم ربات‌های موجود (کتابخانه‌های python/node/…) بدون تغییر به ما وصل شوند، باید endpointهای Bot API (getMe/sendMessage/getUpdates/webhook/answerCallbackQuery/…) را با همان JSON بازپیاده‌سازی کنیم. این خودش یک پروژه مجزا و بزرگ است ولی برای «اکوسیستم» ضروری است. وب‌اپ‌ها (صفحات HTTPS ربات‌ها) بدون تغییر کار می‌کنند چون URL دست ربات‌ساز است.

### بدون تغییر

همه UI ربات/کیبورد/اینلاین/وب‌اپ.

---

## ۱۴. همه APIها، کتابخانه‌ها و سرویس‌های خارجی (غیر MTProto)

### ۱۴.۱. گوگل (Play Services / Firebase / MLKit)

| سرویس | مصرف در اپ | برای ما |
|---|---|---|
| FCM (`firebase-messaging`) + `google-services.json` ×۵ کپی | توکن پوش | پروژه Firebase خودمان + فایل جدید (روتین) |
| Maps SDK + `maps.googleapis.com` + کلید `AIza...` در مانیفست | نقشه لوکیشن | کلید API خودمان (تغییر یک‌خطی مانیفست) |
| Play Billing 8 | خرید Premium/استارز | محصول/مرچنت خودمان در Play Console |
| SafetyNet + `SAFETYNET_KEY` | گواهی سلامت دستگاه | کلید/پروژه خودمان (یا حذف/جایگزینی با Play Integrity خودمان) |
| reCAPTCHA 18.7 | راستی‌آزمایی (وب/فرم‌ها) | کلید خودمان |
| Play Integrity 1.3 | ضدتقلب/لایسنس | پیکربندی خودمان |
| MLKit: language-id / subject-segmentation / image-labeling | تشخیص زبان (ترجمه)، سگمنتیشن، لیبل عکس | بدون کلید خاص؛ همان می‌ماند (آفلاین/عمومی) |
| Cast + MediaRouter | کست به تلویزیون | عمومی؛ می‌ماند |
| Car App | Android Auto | عمومی؛ می‌ماند |
| Wearable + Auth + Vision + Wallet + Location | ساعت، ورود گوگل(?)، QR/بارکد، پرداخت، لوکیشن | عموماً عمومی؛ Wallet/Auth را بازبینی می‌کنیم |
| AppIndexing/DataTransport | زیرساخت فایربیس | با پروژه جدید می‌ماند |

### ۱۴.۲. هواوی (HMS)

ماژول `TMessagesProj_AppHuawei` + `PUSH_TYPE_HUAWEI` + `agconnect`: برای گوشی‌های بدون گوگل. با بک‌اند ما سازگار می‌ماند؛ فقط AppId هواوی خودمان را می‌گذاریم.

### ۱۴.۳. پرداخت کارتی

`PaymentFormActivity` + **Stripe Android 2.0.2** (توکنایز کارت برای invoice ربات‌ها) + `payments.getPaymentForm/sendPaymentForm`. برای ما: کلید publishable خودمان + وب‌هوک سمت سرور + PSP دلخواه (یا حذف پرداخت کارتی در فاز اول و نگه‌داشتن invoice داخلی).

### ۱۴.۴. محتوای/دیتای ثالث

| سرویس | مصرف | برای ما |
|---|---|---|
| Foursquare (`ss3.4sqi.net`) | عکس venueها | کلید API خودمان (یا حذف عکس venue) |
| Yandex Static Maps | fallback نقشه | بدون کلید/محدود؛ قابل نگه‌داشتن موقت |
| YouTube (`youtube.googleapis.com`) / Twitch (`api.twitch.tv/usher`) / Twitter/Instagram | پلیر داخلی لینک‌ها | کلیدهای خودمان (تغییر کوچک) |
| `translate.goo` | (باقیمانده/تست؟) ترجمه عمدتاً سروری است | بخش ۱۵ |
| `tgb.smart-glocal.com` | (یک ارجاع؛ سرویس جانبی ترجمه/GLocal؟) | بازبینی در فاز پیاده‌سازی |
| `promote.telegram.org` | تبلیغات اسپانسری | با بک‌اند ما: تبلیغات خودمان یا هیچی |
| `fragment.com` / `nft.fragment.com` / `toncenter.com` / `tonviewer.com` | یوزرنیم NFT، کیف پول، اکسپلورر TON | **عمومی و بلاک‌چینی؛ بدون تغییر می‌ماند** (وابسته به تلگرام نیست) |
| `t.me` / `telegram.org` / `web.telegram.org` / `messenger.telegram.org` | لینک‌ها/وب‌اپ رسمی/صفحات | `t.me` را بازطراحی می‌کنیم (بخش ۵)؛ بقیه فقط لینک‌اند |
| `attheme.org` | اشتراک‌گذاری تم | عمومی؛ می‌ماند |

### ۱۴.۵. کتابخانه‌های اپن‌سورس مهم (بدون وابستگی سروری)

ExoPlayer/Media3 (فورک)، Markwon (مارک‌داون مقالات)، jlatexmath (فرمول)، Gson/Guava، mp4parser، nanohttpd (سرور لوکال؟)، billing، zxing+Vision (QR)، desugar، WalletConnect? (ندیدم)، Rust: tlottie + wallet-engine + wamr (iwasm؛ اجرای wasm داخل تماس؟ — `libiwasm` نیتیو) + boringssl/ffmpeg/opus/vpx/dav1d/openh264 (پری‌بیلد) + absl/libyuv/tgcalls/webrtc (سورس).

---

## ۱۵. سایر زیرسیستم‌های وابسته به سرور (خلاصه)

| زیرسیستم | فایل‌های کلیدی | API/سرویس | وضعیت برای بک‌اند ما |
|---|---|---|---|
| ترجمه پیام | `TranslateController` + `TL_messages_translateText` | ترجمه **سمت‌سرور** (پرمیوم) | اتصال به پرووایدر ترجمه (گوگل/آزاد) روی سرور ما؛ تشخیص زبان با MLKit می‌ماند |
| پیام اسپانسری | `MessagesController` + `StatsController`(۲۹۲ خط) + `video/VideoAds` | `messages.getSponsoredMessages` + `promote.*` | تبلیغات خودمان یا خالی (UI همان) |
| آمار کانال | `TL_stats` + UI آمار | `stats.getBroadcastStats/...` | شمارنده‌های خودمان (فاز میانی) |
| لوکیشن زنده/نزدیکان | `LocationController` + `TL_contacts_getLocated`(API) | اشتراک‌گذاری زنده + افراد نزدیک | پیاده‌سازی متوسط؛ حریم‌خصوصی دقیق لازم دارد |
| پاسپورت تلگرام | `PassportActivity` + `account.getSecureValue/...` | ذخیره امن مدارک روی سرور | صندوق امن رمزنگاری‌شده خودمان (یا غیرفعال در فاز اول) |
| پس‌کی | `PasskeysController` | فقط AppId رسمی | غیرفعال تا بازطراحی (جزئی) |
| گیفت/حراج/تم | `GiftAuctionController`، `gift_themes`، Stars | اکوسیستم گیفت/استارز | لجر داخلی خودمان (بخش ۱۱/۱۳) |
| بوست کانال | `ChannelBoostsController` | `payments.applyBoost...` + استوری بوست | لجر بوست خودمان |
| درآمدزایی/استیکر درآمد | `ChannelMonetizationLayout` | تسویه TON/استارز | با لجر ما + TON عمومی |
| پوشه‌ها/فیلترها | جداول `dialog_filter*` + `TL_chatlists` | `messages.getDialogFilters/...` | پیاده‌سازی متوسط |
| بیزینس | `ui/Business/*` + جداول | لینک/پاسخ سریع/ساعت کاری | پیاده‌سازی متوسط (فاز میانی) |
| فکت‌چک/کپچا/هشتگ | `FactCheck/Captcha/HashtagSearch` | APIهای کمکی | اختیاری/مرحله‌ای |
| تم/والپیپر/ریموji | `ChatThemeController`، `attheme.org` | اشتراک تم | عمومی؛ می‌ماند |
| آهنگ‌های زنگ | `RingtoneDataStore` | `account.getRingtones/...` | سید اولیه ساده |
| هوش مصنوعی | `AiTonesController`، `TL_aicompose` | قابلیت‌های AI سروری تلگرام | بازطراحی با مدل خودمان یا حذف موقت (UI مشروط است) |
| انجمن‌ها | `TL_communities` | — | مرحله‌ای |
| بتای داخلی | `BetaUpdate` + `BETA_*_URL` | آپدیتر نسخه HockeyApp | مال خودمان می‌شود (trivial) |
| تماس تصویری چندنفره/لایو | بخش ۱۰ | SFU | سخت‌ترین بخش (بخش ۲۰) |

---

## ۱۶. فهرست تمام وابستگی‌های اپ به زیرساخت تلگرام (چک‌لیست مهاجرت)

**دسته A — باید در سرور ما بازپیاده‌سازی شود (هسته):** handshake/RSA، احراز هویت+SMS، difference/pts/qts/seq، پیام/دیالوگ/مدیا/فایل، گروه/کانال/تاپیک، مخاطب/یوزرنیم/پرایوسی، سکرت‌چت (رله)، تماس (سیگنال+reflector+SFU)، پوش (ثبت توکن+ارسال)، سشن‌ها، 2FA/SRP، استوری، استیکر (کاتالوگ)، ترجمه، اسپانسر(اختیاری)، آمار، بوست/گیفت/استارز (لجر)، پرداخت/اینویس، پاسپورت(اختیاری)، Bot API (سرویس جدا)، `getConfig/getAppConfig` (پرچم‌های قابلیت!).

**دسته B — کلید/پروژه خودمان جایگزین می‌شود (تغییر کوچک کلاینت، بدون اثر ظاهری):** Firebase/FCM، HMS، Maps، Stripe، Foursquare، YouTube/Twitch، SafetyNet/Integrity/reCAPTCHA، Play Billing، `api_id` (۴ ← آیدی خودمان)، RSA و IP دیتاسنتر (نیتیو)، `t.me` (ریزالور)، BETA URLها، `SAFETYNET_KEY/GOOGLE_AUTH_CLIENT_ID` در `BuildVars.java`.

**دسته C — بدون تغییر می‌ماند (عمومی/لوکال/بلاک‌چین):** کل UI، SQLite و کش‌ها، NotificationCenter، ExoPlayer، tlottie، E2E سکرت‌چت و تماس ۱به۱، پروکسی (فقط به DC ما وصل می‌شود)، مولتی‌اکانت، ویجت/وِر، تم‌ها، TON/wallet/fragment (شبکه عمومی TON)، WebApp ربات‌ها (URL دست ربات‌ساز)، MLKit آفلاین، Cast/Car.

---

## ۱۷. فهرست فایل‌ها و کلاس‌های مهم (نقشه راهنما)

### هسته شبکه و API
- `org/telegram/tgnet/ConnectionsManager.java` — دروازه همه درخواست‌ها
- `org/telegram/tgnet/TLRPC.java` (+`tl/*.java`) — اسکیمای Layer 232
- `org/telegram/tgnet/{TLObject,TLMethod,SerializedData*,NativeByteBuffer,TLClassStore}.java`
- `jni/tgnet/{ConnectionsManager,Datacenter,Handshake,Connection,ConnectionSocket,ConnectionSession,Request,Config,MTProtoScheme,ApiScheme}.cpp` + `jni/openssl/aes_ige.c` + `jni/TgNetWrapper.cpp`

### سرویس‌ها (per-account)
- `messenger/{MessagesController,MessagesStorage,SendMessagesHelper,SecretChatHelper}.java`
- `messenger/{UserConfig,ContactsController,UserNameResolver,FileLoader,FileUploadOperation,FileLoadOperation,FileRefController,DownloadController,MediaDataController,NotificationsController,PushListenerController,MediaController,LocationController,TranslateController,StatsController,BillingController,MemberRequestsController,TopicsController,ChannelBoostsController,SavedMessagesController,ProxyRotationController,DownloadRequestPacer,AuthTokensHelper,UnconfirmedAuthController,PasskeysController,FingerprintController}.java`
- `messenger/voip/*` (تماس)، `ui/Stories/StoriesController.java`، `ui/Stars/StarsController.java`

### ورود و شروع
- `ui/{LoginActivity,IntroActivity,LaunchActivity,SessionsActivity,PassportActivity,PasscodeActivity}.java`
- `messenger/{ApplicationLoader,BuildVars}.java` + `GcmPushListenerService` + رسیرورها

### UI (بدون تغییر می‌ماند — برای مرجع)
- `ui/{ChatActivity,DialogsActivity,ProfileActivity,ChatEdit*,ChannelCreate*,GroupCallActivity,PaymentFormActivity,ContactsActivity,Settings*}.java` + `ui/{Cells,Components,Adapters,ActionBar,Stickers,Stories,Stars,Business,bots,Components/Premium,Components/voip}/*`

### بیلد/پیکربندی (نقاط تغییر کلاینت در فاز بعد)
- `gradle.properties` (`APP_PACKAGE`, `APP_VERSION_*`)، `TMessagesProj/build.gradle`، `Dockerfile`
- `TMessagesProj*/google-services.json` (۵ کپی!)، `TMessagesProj/config/{release,debug}/AndroidManifest*.xml` (کلید Maps، authorities با `${applicationId}`)
- `TMessagesProj/src/main/java/org/telegram/messenger/BuildVars.java` (کلیدها/URLها)
- `.github/workflows/build-apk.yml` (پایپ‌لاین بیلد ما — آماده برای ری‌بیلد پس از تغییرات نیتیو)

---

## ۱۸. نقشه راه جایگزینی بک‌اند (پیشنهادی، ۵ فاز)

> اصل حاکم: در هر فاز، UI و رفتار ظاهری عیناً حفظ شود؛ فقط «طرف سرورِ» همان متدها به‌تدریج کامل می‌شود. ترتیب بر اساس ارزش/ریسک:

### فاز ۰ — آماده‌سازی کلاینت (کوتاه، کم‌ریسک)
- پچ نیتیو: جدول DC ← تک‌DC ما (IP/پورت) + کلید RSA خودمان + ری‌بیلد `.so` با همین پایپ‌لاین.
- `APP_PACKAGE`/FIR/HMS/Maps/Stripe/`api_id` ← مقادیر ما؛ `t.me` ← ریزالور ما (یا دامین خودمان).
- حالت توسعه: درگاه SMS آزمایشی (کد ثابت/پنل) + فلگ `getAppConfig` برای خاموش‌کردن موقت قابلیت‌های فازهای بعد (بدون تغییر UI؛ فقط دکمه‌ها همان رفتار «به‌زودی» استاندارد را می‌گیرند — با احتیاط و حداقل).

### فاز ۱ — MVP پیام‌رسان (هسته حیاتی)
MTProto transport کامل (TCP+obfuscation، سشن، salt، container، gzip، ack) + handshake + `auth.*` (کد/ورود/ثبت‌نام/2FA-SRP/نشست/QR) + `users/contacts` پایه + `messages.sendMessage/getHistory/getDialogs` + آپدیت زنده و `getDifference` (تک‌نوع، بدون کانال) + آپلود/دانلود فایل + `registerDevice` + ارسال پوش + `getConfig/getAppConfig`. **خروجی:** چت ۱به۱ + فایل + پوش، با همان UI.

### فاز ۲ — گروه/کانال/مدیای کامل
`channels.*` کامل (ادمین/بن/لینک/لاگ/گیگا)، تاپیک/انجمن، `getChannelDifference`، فوروارد/ادیت/حذف/ری‌اکشن/پین/زمان‌بندی/درافت، سرچ، فولدرها، مخاطبین کامل + یوزرنیم + بلاک/پرایوسی، استتوس آنلاین، سکرت‌چت (رله + E2E دست‌نخورده)، استوری کامل، استیکر/ایموجی (کاتالوگ ما)، نظرسنجی، لوکیشن زنده، تم/والپیپر/رینگتون.

### فاز ۳ — تماس‌ها
سیگنالینگ `phone.*` + `getCallConfig` + reflector/TURN خودمان برای ۱به۱ (E2E حفظ)؛ سپس SFU سازگار tgcalls برای گروه/لایو (پروتوتایپ → پایدارسازی). بزرگ‌ترین ریسک: بخش ۲۰.

### فاز ۴ — اکوسیستم و درآمد
Bot API سازگار (سرویس HTTP جدا) + وب‌اپ‌ها (بدون تغییر)، پرداخت/اینویس + لجر استارز/گیفت/بوست + Premium/entitlement خودمان، ترجمه (پرووایدر)، آمار کانال، اسپانسر (اختیاری)، پاسپورت (اختیاری)، بیزینس، nearby، AI (اختیاری).

### فاز ۵ — سخت‌سازی و مقیاس
چند-DC واقعی (با مهاجرت استاندارد `*_MIGRATE_*`)، پنل ضداسپم/مدیریت، مانیتورینگ/لاگ، تست سازگاری خودکار (کلاینت ما به‌عنوان oracle + تست‌سوئیت difference/edgeها)، مستندسازی قرارداد Layer 232 ما (فریز نسخه تا ارتقای کنترل‌شده).

---

## ۱۹. مشکلات و محدودیت‌های احتمالی (ریسک‌ها)

1. **حجم سطح API:** ~۲۰۰۰+ کلاس اسکیما؛ پوشش ۱۰۰٪ در کوتاه‌مدت ناممکن است → راه‌حل: فازبندی + قرارداد «خطای استاندارد» برای متدهای پیاده‌نشده (کلاینت خطای تمیز را بهتر از رفتار اشتباه هندل می‌کند).
2. **سمانتیک difference:** کوچک‌ترین انحراف در pts/qts/seq → لوپ سینک/گم‌شدن پیام. نیازمند تست‌سوئیت سنگین و ایزوله‌سازی (ابتدا تک‌DC).
3. **تماس گروهی/SFU:** پروتکل سروری مستند نیست؛ نیازمند مهندسی معکوس + تست رفتاری. (تماس ۱به۱ بسیار کم‌ریسک‌تر است.)
4. **E2E در برابر قابلیت‌ها:** سکرت‌چت و تماس ۱به۱ ذاتاً E2E می‌مانند (خوب)؛ اما یعنی سرور ما هم مثل تلگرام به محتوای آن‌ها دسترسی ندارد (محدودیت مانیتورینگ/بازیابی).
5. **هویت‌ها از صفر:** شماره‌ها/یوزرنیم‌ها/کانال‌های تلگرام قابل مهاجرت نیستند؛ نام‌کاربری‌ها ممکن است قبلاً گرفته شده باشند (نیازمند سیاست رزرو/تأیید).
6. **هزینه پیامک و deliverability** در ایران/جهان (مسیرهای جایگزین: تماس صوتی، کد داخل‌اپ روی دستگاه دوم، حالت توسعه).
7. **پوش iOS آینده:** فعلاً فقط اندروید داریم؛ ولی APNs را در طراحی Push Gateway از اول ببینید.
8. **اعتماد و امنیت:** بازبینی مستقل رمزنگاری/کلیدها، HSM/KMS برای RSA، secret management، rate-limit و ضداسپم از روز اول (کلاینت روی `FLOOD_WAIT` حساب می‌کند).
9. **حقوقی/لایسنس:** سورس تحت GPL است → انتشار کد فورک (همین مخزن عمومی) الزامی است؛ نام/لوگوی Telegram متعلق به تلگرام است → برای محصول نهایی ری‌برند کامل (نام «Telegram 2» هم ریسک حقوقی دارد)؛ کاتالوگ استیکر رسمی قابل کپی نیست.
10. **قفل نسخه Layer:** تا وقتی سرور روی 232 فریز است، نباید کلاینت را از روی آپ‌استریم آپدیت کرد (تغییر Layer = شکست قرارداد). فرآیند ارتقای هم‌زمان کلاینت+سرور لازم است.
11. **ساعت سرور (NTP):** اعتبارسنجی `msg_id` به ساعت دقیق وابسته است؛ انحراف → قطع ارتباط کلاینت‌ها.
12. **فایل‌رفرنس/access_hash:** اگر فاز اول ساده‌سازی شود (غیرمنقضی/تک‌DC)، بعداً با مقیاس چندDC باید مهاجرت تمیز طراحی شود.

---

## ۲۰. پیشنهاد معماری بک‌اند مستقل

```
                    ┌──────────────────────────────┐
                    │        Edge / Gateway         │
                    │  MTProto TCP (+obfuscation)   │  ← تنها ورودی کلاینت
                    │  سشن، auth_key، salt، ack     │
                    └──────┬───────────┬───────────┘
         ┌─────────────────┘           └─────────────────┐
┌────────▼────────┐   ┌──────────────┐   ┌───────────────▼───┐
│  Auth Service   │   │ Updates/Diff │   │  Dialog/Message   │
│ SMS/2FA/SRP/QR/ │   │ pts/qts/seq  │   │ Store (ScyllaDB/  │
│ sessions/tokens │   │ event-sourced│   │ Cassandra + PG)   │
└────────┬────────┘   └──────┬───────┘   └────────┬──────────┘
┌────────▼────────┐   ┌──────▼───────┐   ┌────────▼──────────┐
│ Users/Contacts/ │   │ Files (S3 +  │   │ Push Gateway      │
│ Groups/Channels │   │ CDN)، thumb  │   │ FCM/HMS/(APNs)    │
│ Usernames/Social│   │ worker       │   │ queue + template  │
└─────────────────┘   └──────────────┘   └───────────────────┘
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Call Signal  │  │ Reflector/   │  │ Group SFU    │  │ Bot API (HTTP)│
│ + getCallCfg │  │ TURN (1-1)   │  │ (tgcalls-    │  │ Bot-compatible│
└──────────────┘  └──────────────┘  │  compat)     │  └──────────────┘
                                     └──────────────┘
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Payments/    │  │ Translate    │  │ Moderation/  │  │ Obs/Admin    │
│ Stars ledger │  │ provider +   │  │ Anti-spam/   │  │ metrics/logs │
│ + PSP/Stripe │  │ cache        │  │ FLOOD policy │  │ + support    │
└──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘
```

**توصیه‌های تکنولوژی (پیشنهاد، نه الزام):**
- Gateway/transport: Go یا Rust (هزاران کانکشن هم‌زمان، کنترل دقیق باینری)؛ کدجن TL از روی همان اسکیمای Layer 232 (فایل‌های `.tl` رسمی + diff با کلاس‌های جاوای کلاینت به‌عنوان مرجع واحد حقیقت).
- Updates: event-sourced + ترتیب‌دهنده مرکزی برای pts/seq (تک‌رایتِر منطقی در فاز اول؛ شاردینگ بعدی).
- ذخیره پیام/مدیا: ScyllaDB/Cassandra (write-heavy) + PostgreSQL برای متادیتای رابطه‌ای (یوزر/گروه/ممبرشیپ) + Redis برای سشن/ریت‌لیمیت/حضور.
- فایل: S3-compatible (مثل MinIO/SeaweedFS) + CDN + صف thumb؛ قرارداد `photoSize/documentAttribute` دقیقاً مثل تلگرام.
- SFU تماس گروهی: توسعه اختصاصی سازگار tgcalls (فاز ۳)؛ تا آن زمان API تماس ۱به۱ + reflector.
- قرارداد نسخه: `getAppConfig` ما = سوییچ قابلیت‌ها؛ Layer روی 232 فریز تا ارتقای برنامه‌ریزی‌شده.

**تغییرات حداقلی کلاینت در فاز اجرا (بدون هیچ اثر ظاهری):** جدول DC + RSA (نیتیو)، `api_id`، ۵ فایل `google-services.json`، کلید Maps در مانیفست، کلیدهای Stripe/Foursquare/YouTube/Twitch/SafetyNet/reCAPTCHA، ریزالور `t.me`/دامین، BETA URLها، نام/آیکون نهایی (ری‌برند حقوقی). همه این‌ها قبلاً در همین مخزن/پایپ‌لاین تمرین شده‌اند (workflow بیلد + پکیج جدا آماده است).

---

## ۲۱. جمع‌بندی و قدم بعدی پیشنهادی

1. این گزارش = مبنای طراحی سرور. قدم بعدی منطقی: **«سند قرارداد Layer 232 ما»** — استخراج متدبه‌متدِ subset فاز ۱ از روی همین کلاس‌های `TLRPC/TL_*` + رفتارهای خطا، به‌صورت چک‌لیست قابل تست.
2. سپس: اسکلت Gateway (handshake + یک `auth.sendCode` آزمایشی) و اتصال همین APK (با پچ DC/RSA) به آن — اولین «سلام» مستقل.
3. موازی: تهیه کلیدها/پروژه‌ها (Firebase/HMS/Maps/Stripe/SMS) و تصمیم ری‌برند نهایی.

---
*پایان گزارش مأموریت ۱ — آماده برای بازبینی و شروع طراحی فاز ۰/۱.*
