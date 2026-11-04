# فاز ۵ — مخاطبین، پروفایل و جست‌وجوی سراسری

شاخه: `arena/76249c66-telegram` · تاریخ: ۲۰۲۶-۱۱-۰۴

> **وضعیت فاز ۴ (ثبت‌شده):** «پیاده‌سازی قابلیت‌های اصلی پیام‌رسانی متنی انجام شده؛ اعتبارسنجی نهایی APK و Emulator باقی مانده است.»
> **وضعیت فاز ۳:** پیاده‌سازی‌شده؛ اعتبارسنجی APK باقی مانده — تأییدشده اعلام نمی‌شود.

## ۰. دو پیش‌نیاز این مرحله

### الف) اصلاح ماسک نتایج جست‌وجو
`MediaDataController.getMask()` ماسک را این‌گونه می‌سازد: بیت ۱ = «نتایج بیشتری هست»، بیت ۲ = «چیزی برای برگشت هست».
کد قبلی همیشه `0` می‌فرستاد، یعنی نوار جست‌وجو فکر می‌کرد نه بعدی هست نه قبلی.
اکنون: `mask = (more ? 1 : 0) | (index > 0 ? 2 : 0)` و `more = results.size() >= 50` (ظرفیت صفحهٔ جست‌وجو).

### ب) وابستگی‌های باقی‌ماندهٔ ChatActivity
فهرست کامل در `phase4-completion-fa.md` §۴ و `phase4-chat-fa.md` §۴ (رسانه، نظرسنجی/مکان/مخاطب/تماس،
فوروارد رسانه، زمان‌بندی، چت مخفی، ربات/واکنش/ترجمه، تاپیک‌ها، `MessagesStorage` و `ConnectionsManager`).
این فهرست در `STATUS-fa.md` و همین‌جا ارجاع داده شده و هیچ‌کدام «کامل‌شده» اعلام نشده است.

## ۱. بک‌اند

| مورد | جزئیات |
|---|---|
| مخاطبین | `GET/POST /contacts`، `DELETE /contacts/{id}` (از قبل بود؛ اکنون با تست) |
| پروفایل | `GET /users/{id}` با فیلتر حریم خصوصی، `PATCH /users/me` (نام/بیو/آواتار) |
| حریم خصوصی | ستون‌های `users.last_seen_privacy` و `users.search_privacy` (`everybody|contacts|nobody`)، `PATCH /users/me/privacy` |
| نمایش حضور | `is_online`، `presence_visible`، و پاک‌سازی `last_seen_at` بر اساس سیاست + رابطهٔ مخاطب + مسدودسازی |
| جست‌وجوی کاربران | `GET /users/search` با اعمال `search_privacy` (nobody حذف، contacts فقط برای مخاطب) |
| جست‌وجوی سراسری | **جدید** `GET /search?q=` → `{users, chats}`؛ چت‌ها فقط عضویت‌های خود کاربر |
| همگام‌سازی پروفایل | رویداد `profile.updated` برای مخاطبین و هم‌گفتگوها |
| مسدودسازی | `POST/DELETE /users/{id}/block` + `GET /users/blocked` و اثر آن روی حضور |
| Migration | `cb147419e222` (ستون‌های حریم خصوصی) |

## ۲. اندروید

| فایل | کار |
|---|---|
| `platform/repo/UserRepository.java` (جدید) | مخاطبین، پروفایل، ویرایش پروفایل/حریم خصوصی، مسدودسازی، جست‌وجوی کاربران و سراسری |
| `platform/tl/PlatformContactsSync.java` (جدید) | تبدیل مخاطبین API به `TLRPC.User`، نصب در `ContactsController`، ساخت `UserFull` و انتشار `userInfoDidLoad`، جست‌وجوی سراسری، هندل `profile.updated` |
| `messenger/ContactsController.java` | شاخهٔ محافظت‌شده در `loadContacts(...)` + متد جدید `platformSetContacts(...)` (dict/sections/order) |
| `messenger/MessagesController.java` | شاخهٔ محافظت‌شده در `loadFullUser(...)` |
| `ui/Adapters/SearchAdapter.java` | شاخهٔ محافظت‌شده در `searchDialogs(...)`؛ نتایج از API با محافظ نسل‌شمار (جلوگیری از بازنویسی نتیجهٔ قدیمی) |
| `platform/PlatformBridge.java`, `messenger/BuildVars.java` | `users()`، `profile.updated` → `PlatformContactsSync`، فلگ `CONTACTS_FROM_PLATFORM` (فقط Debug) |
| `platform/tl/PlatformChatSync.java` | اصلاح ماسک جست‌وجو |

## ۳. تست‌ها

| مورد | نتیجه |
|---|---|
| `tests/test_contacts_phase5.py` (جدید، ۸ تست) | مخاطبین add/list/remove، حریم خصوصی last_seen (nobody/contacts)، حریم خصوصی جست‌وجو، نبود فیلد خصوصی در نتایج، جست‌وجوی سراسری (کاربر+چت، عدم نشت چت غریبه)، انتشار `profile.updated`، اثر مسدودسازی، اعتبارسنجی مقادیر privacy |
| کل بک‌اند | **۱۵۰ سبز** (۱۴۲ قبلی + ۸) |
| `tools/android_contacts_probe.py` (جدید) | **۱۵/۱۵** روی سرور زنده با `demo_alice`/`demo_bob` |
| `check_chat_no_mtproto.py` | **۱۳ نقطهٔ ورود** (۳ مورد جدید: contacts/profile/global search) پیش از هر فراخوانی MTProto |
| `check_android_phase1.py` / `check_tl_contract.py` | OK — `openapi.json` با ۹۹ عملیات |

## ۴. آنچه تأیید نشده (بدون ادعای اضافه)
1. **کامپایل/APK/امولاتور انجام نشد** (نه `javac`، نه Android SDK، شبکهٔ Maven/Google بسته) →
   نمایش فهرست مخاطبین، صفحهٔ پروفایل، آواتار و نتایج جست‌وجو **در اپ تأیید نشده**.
2. **ویرایش نام/نام‌خانوادگی/نام‌کاربری** به‌صورت جداگانه پیاده نشد: بک‌اند `display_name` و `bio` و `avatar_file_id`
   را می‌پذیرد؛ تغییر `username` یکتا هنوز اندپوینت اختصاصی ندارد (نام کاربری فقط خواندنی است).
3. **مدیریت آواتار در UI** (انتخاب عکس و آپلود از پروفایل) وصل نشده؛ فقط نمایش آواتار (فاز ۳/این فاز) کار می‌کند.
4. صفحهٔ پروفایل کامل (`ProfileActivity`) تنها دادهٔ `UserFull.about` را از بک‌اند می‌گیرد؛ بخش‌های دیگر
   (رسانه‌های مشترک، تنظیمات، دکمهٔ بلاک در UI) هنوز از مسیر تلگرام می‌آیند.
5. فیلتر «مخاطبین» در جست‌وجو یک‌طرفه بررسی می‌شود (هر یک از دو طرف که دیگری را مخاطب کرده باشد).
