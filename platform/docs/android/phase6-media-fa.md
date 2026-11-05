# فاز ۶ — رسانه و سیستم فایل (گزارش وضعیت)

> فقط چیزهایی «انجام‌شده» نامیده می‌شوند که در همین محیط اجرا و تست شده‌اند.
> **کامپایل APK و اجرای Emulator در این محیط ممکن نیست** (نه JDK/Android SDK و نه دسترسی به
> `dl.google.com` / `repo1.maven.org` / `services.gradle.org` / `api.adoptium.net`).
> بنابراین اعتبارسنجی در سطح API، کد و بررسی ایستا انجام شده است، نه اجرای واقعی اپ.

## ۱) زیرساخت فایل — تکمیل و تست‌شده

| قابلیت | وضعیت | محل |
|---|---|---|
| آپلود تک‌مرحله‌ای با پیشرفت | ✅ | `POST /api/v1/files` |
| آپلود چندبخشی (multipart) | ✅ | `POST /files/multipart/init` · `PUT .../parts/{index}` · `POST .../complete` |
| تشخیص نوع واقعی از magic bytes | ✅ | `backend/app/media.py` |
| رد محتوای ناهم‌خوان با kind | ✅ | `415` |
| سقف حجم ۲۰۰MB در همه مسیرها | ✅ | `413` |
| جلوگیری از آپلود تکراری | ✅ | مالک + sha256 + kind → همان رکورد |
| نام فایل امن | ✅ | `safe_filename` |
| ذخیره خارج از web root، کلید محتوایی | ✅ | `LocalStorage` |
| کنترل دسترسی (مالک/عضو چت) | ✅ | `_can_access` |
| بستن دورزدن با پرچم `is_public` | ✅ | فقط با `PUBLIC_FILES_ENABLED=true` (پیش‌فرض خاموش) |
| توکن دانلود کوتاه‌عمر و فایل‌محور | ✅ | `POST /files/{id}/download-token` → `GET /files/{id}/stream?token=…` |
| ثبت رویداد امنیتی رد دسترسی | ✅ | `file.download_denied` / `file.download_token_denied` |
| محدودیت نرخ اختصاصی آپلود | ✅ | `UPLOAD_RATE_LIMIT_REQUESTS` (پیش‌فرض ۶۰/دقیقه به‌ازای هر حساب) |
| ابعاد تصویر برای چیدمان حباب | ✅ | `width`/`height` با Pillow در آپلود و multipart |
| هم‌خوانی حجم و صحت (sha256 + اندازه) | ✅ | در multipart پیش از commit |
| پاک‌سازی فایل یتیم | ✅ | `python -m app.cli files-gc --older-than-hours 24` |
| عدم افشای مسیر ذخیره‌سازی | ✅ | هیچ پاسخ API مسیر را برنمی‌گرداند |

## ۲) Realtime

| رویداد | وضعیت |
|---|---|
| `media.uploaded` | ✅ به مالک پس از آپلود |
| `media.ready` | ✅ به اعضای چت هنگام ساخت پیام رسانه‌ای |
| `media.failed` | ✅ به آپلودکننده در خطای ۴۱۳/۴۱۵ |
| `message.media` | ✅ به اعضای چت (همان شناسه پیام، بدون تکرار) |
| `media.deleted` | ✅ هنگام حذف فایل توسط مالک |

هر فریم شناسه یکتای رویداد دارد. ارسال دوبارهٔ یک پیام (همان `client_message_id`) پیام دوم
نمی‌سازد؛ آپلود دوبارهٔ همان بایت‌ها فایل دوم نمی‌سازد.

## ۳) سمت اندروید

- `platform/repo/MediaRepository.java` (جدید): آپلود استریمی با پیشرفت، **لغو**، multipart خودکار
  برای > ۸MB، دانلود استریمی با کش، تلاش دوباره با فراخوانی مجدد.
- `platform/tl/PlatformMediaSync.java` (جدید):
  - `sendMedia()`: آپلود → ساخت پیام با `file_id` از مسیر واقعی `sendMessage`.
  - `applyMedia(message, row, dialogId)`: تبدیل ردیف بک‌اند به ساختار TL موجود —
    عکس: `TL_messageMediaPhoto` + `TL_photoSize` با ابعاد واقعی؛
    سند/ویدیو: `TL_messageMediaDocument` با `TL_document`، نام فایل، حجم، MIME و
    `TL_documentAttributeFilename` / `ImageSize` / `Video` تا کارت سند بدون شبکه ساخته شود.
  - دانلود خودکار به `cacheDir/platform_media/{fileId}.ext` و ست‌کردن `attachPath`.
  - `cancelUpload(path)` و `uploadProgress(path)`؛ پیشرفت در نقشه‌ای محلی نگه داشته می‌شود
    (کانال `fileUploadProgressChanged` تلگرام آرگومان `ImageLocation` می‌خواهد و با کلید مسیر
    ما کرش می‌کرد، بنابراین عمداً استفاده نشد).
- `platform/mapper/DtoMapper.java`: هر پیام رسانه‌ای از `applyMedia` عبور می‌کند.
- `SendMessagesHelper.sendMessage`: شاخهٔ `isPlatformMedia(...)` **قبل از** هر MTProto برای عکس و سند.
- `BuildVars.MEDIA_FROM_PLATFORM = BuildConfig.DEBUG`، `PlatformBridge.media()`.

## ۴) تست‌ها (اجراشده در همین محیط)

- `backend/tests/test_media_phase6.py` — **۲۱ تست سبز**: sniff/kind، رد محتوای دروغگو،
  sanitize نام، dedupe، رد kind ناشناخته، دانلود عضو، رد غریبه (IDOR)، قطع دسترسی پس از حذف پیام،
  multipart کامل/فقط‌مالک/عدم تطابق حجم/سقف حجم، آپلود نیمه‌کاره و **ازسرگیری پس از قطع**،
  جلوگیری از پیام تکراری، `media.failed` روی WebSocket، `media.uploaded`،
  `message.media` + `media.ready`، `media.deleted`، توکن دانلود (فایل‌محور/دستکاری‌شده/منقضی/
  بی‌مجوز)، بستن دورزدن `is_public`، سهمیهٔ آپلود، و متادیتای فایل در History (همان چیزی که
  اندروید برای ساخت حباب می‌خواند).
- کل مجموعهٔ بک‌اند: **۱۷۱ تست سبز** (بدون رگرسیون؛ ۱۵۰ → ۱۶۳ → ۱۷۱).
- `tools/android_media_probe.py` روی سرور زنده با `demo_alice`/`demo_bob` + حساب سوم:
  **۲۱ از ۲۱ موفق** (شامل دریافت `message.new`+`message.media`+`media.ready`، دانلود عضو،
  رد غریبه، ویدیو، سند با نام/حجم/MIME، رد محتوای دروغگو، سقف حجم، تلاش دوباره، عدم آپلود تکراری،
  لغو آپلود نیمه‌کاره، ازسرگیری پس از قطع، عدم تکرار پیام، `media.failed`، متادیتای فایل در
  History، توکن دانلود، و قطع دسترسی پس از حذف پیام).
- پروب‌های رگرسیون فازهای قبل: `android_chat_probe` **۱۲/۱۲**، `android_contacts_probe` **۱۵/۱۵**.
- بررسی‌های ایستا: `check_android_phase1` ✅ (**۱۰۴ operation** / ۶۳ schema)،
  `check_tl_contract` ✅ (فیلدهای ارث‌بری‌شدهٔ TL هم اکنون بررسی می‌شوند)،
  `check_dialogs_no_mtproto` ✅، `check_chat_no_mtproto` ✅ (**۱۴ نقطهٔ ورود**؛
  `MediaRepository` هیچ نماد MTProto ندارد و `PlatformMediaSync` فقط ساختار TL محلی می‌سازد).
- `javalang` روی فایل‌های جدید/تغییریافتهٔ جاوا: parse موفق (بدون کامپایل واقعی).

## ۵) وابستگی‌های باقی‌مانده به MTProto

- صدا (voice)، استیکر، GIF، موقعیت، نظرسنجی: مسیر اصلی دست‌نخورده.
- ویدیوی دارای `videoEditedInfo` (ترنسکد/برش) از مسیر تلگرام می‌رود.
- فوروارد رسانه، پیام زمان‌بندی‌شده، چت مخفی، ربات‌ها، ترجمه: دست‌نخورده.
- آواتار: بارگذاری از UI به endpoint پلتفرم وصل نیست (فقط نمایش).

## ۶) استیکر و ایموجی

معماری فعلی تلگرام برای استیکر به `MediaDataController` + `StickerSet`هایی وابسته است که از
سرورهای تلگرام می‌آیند؛ هیچ زیرساخت مستقل و قابل‌اتکایی برای استیکر در بک‌اند ما وجود ندارد
(نه جدول sticker set، نه endpoint، نه مجوز فایل). بنابراین:
- هیچ استیکری از API/سرور رسمی تلگرام استفاده نشد؛
- هیچ استیکر پرمیومی کپی نشد؛
- قابلیت استیکر **اضافه نشد** و به‌عنوان «پشتیبانی کامل» ادعا نمی‌شود؛
- ایموجی سفارشی/متحرک (AnimatedEmoji) هم به مرحلهٔ مستقل موکول شد.

## ۷) محدودیت‌ها و کارهای ناتمام (صادقانه)

1. **رندر نهایی حباب رسانه تأیید نشده**: ساختار TL، ابعاد و `attachPath` ساخته می‌شود و کارت
   سند باید بومی رندر شود، اما بدون کامپایل/اجرا نمی‌توان گفت تصویر داخل حباب درست نمایش
   داده می‌شود.
2. **نوار پیشرفت آپلود/دانلود در UI وصل نشده**: پیشرفت در Repository و `uploadProgress()` موجود
   است، اما هیچ ویویی به آن bind نشده (کانال پیشرفت تلگرام با کلید مسیر محلی سازگار نبود).
3. **دکمه‌های لغو/تلاش دوباره در UI وصل نشده‌اند**؛ منطق آن‌ها (لغو واقعی، تلاش دوباره) پیاده و
   تست شده است.
4. **دوربین/گالری/انتخاب فایل**: هیچ صفحهٔ جدیدی ساخته نشد؛ مسیر موجود `sendMessage` عکس و سند
   را به پلتفرم می‌برد، اما انتخاب‌گر/مجوز دوربین به‌صورت اختصاصی اضافه نشده است.
5. پیش‌نمایش قبل از ارسال و ویدیوی داخل چت (پخش) کامپایل/اجرا نشده‌اند.

## ۸) بررسی امنیتی

- IDOR: دانلود با بررسی مالکیت/عضویت؛ multipart مقید به مالک `upload_id`؛ توکن دانلود
  فایل‌محور و کاربر‌محور با HMAC و انقضا (تست دستکاری/انقضا/فایل دیگر).
- محتوای جعلی: نوع از بایت‌ها تعیین می‌شود؛ ادعای کلاینت فقط برای رد کردن به کار می‌رود.
- هدر: نام فایل پاک‌سازی می‌شود؛ `X-Content-Type-Options: nosniff` روی استریم.
- افشای مسیر: هیچ API مسیر ذخیره‌سازی را برنمی‌گرداند.
- نرخ: محدودیت عمومی ۳۰۰/دقیقه + سهمیهٔ آپلود ۶۰/دقیقه به‌ازای حساب.
- باقی‌مانده: پرچم `is_public` هنوز در دیتابیس است (پیش‌فرض خاموش و در `_can_access` بی‌اثر)؛
  پیشنهاد حذف کامل در فاز بعد. `media.failed` فقط به آپلودکننده می‌رود (نه به چت).

## ۹) فایل‌های جدید/تغییریافته

جدید: `backend/app/media.py`، `backend/app/file_tokens.py`، `backend/tests/test_media_phase6.py`،
`tools/android_media_probe.py`، `platform/repo/MediaRepository.java`،
`platform/tl/PlatformMediaSync.java`، همین گزارش.

تغییریافته: `backend/app/storage.py`، `backend/app/routers/files.py`،
`backend/app/routers/messages.py`، `backend/app/schemas.py` (`MultipartInit`, `MultipartOut`,
`DownloadToken`, `FileBrief`)، `backend/app/services.py` (`attach_file_payloads`)،
`backend/app/config.py`، `backend/app/cli.py`، `backend/tests/test_files.py`،
`tools/check_chat_no_mtproto.py`، `tools/check_tl_contract.py`، `docs/openapi.json`،
`messenger/SendMessagesHelper.java`، `messenger/BuildVars.java`، `platform/PlatformBridge.java`،
`platform/mapper/DtoMapper.java`.

## ۱۰) جمع‌بندی

زیرساخت فایل و رسانه سمت سرور (آپلود، اعتبارسنجی، multipart، توکن دانلود، سهمیهٔ نرخ،
کنترل دسترسی، رویدادها، پاک‌سازی) **تکمیل و تست شده** است. اتصال اندروید در سطح Repository،
مسیر ارسال و ساخت ساختار رسانه **نوشته و از نظر ایستا بررسی شده**، اما نمایش نهایی حباب،
نوار پیشرفت، لغو/تلاش دوباره در UI و پخش ویدیو **اعتبارسنجی APK نشده‌اند**.
