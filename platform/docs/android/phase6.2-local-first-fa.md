# فاز ۶.۲ — ارسال Local-First رسانه + بررسی Build واقعی

## ۱) بررسی معماری فعلی (قبل از تغییر کد)

| پرسش | یافته (با مرجع) |
|---|---|
| پیام محلی چگونه ساخته می‌شود؟ | `SendMessagesHelper.createSendingGramTransfer(...)` الگوی کامل است: `message.local_id = message.id = getUserConfig().getNewMessageId()`، `flags |= 256`، `peer_id`، `date = getConnectionsManager().getCurrentTime()`، `send_state = MESSAGE_SEND_STATE_SENDING`، سپس `getMessagesStorage().putMessages(...)`، `getMessagesController().updateInterfaceWithMessages(dialogId, objects, 0)`، `NotificationCenter.dialogsNeedReload`، `putToSendingMessages(message, false)`. (خطوط ۱۸۷۱–۱۹۱۰) |
| وضعیت ارسال چگونه مدیریت می‌شود؟ | `MessageObject.MESSAGE_SEND_STATE_SENDING / SENT / SEND_ERROR` روی `messageOwner.send_state`؛ دو نقشهٔ داخلی `sendingMessages` (`SparseArray<TLRPC.Message>`) و `uploadingMessages` (`putToUploadingMessages` / `removeFromUploadingMessages`). |
| `MessageObject` چگونه به‌روزرسانی می‌شود؟ | با `NotificationCenter.updateInterfaces` (+ `MessagesController.UPDATE_MASK_*`) و `messageSendError`؛ پس از تأیید سرور نیز `messageReceivedByServer` استفاده می‌شود. |
| لغو و تلاش مجدد فعلی؟ | لغو: `SendMessagesHelper.cancelSendingMessage(MessageObject)` (فراخوانی از `ChatActivity`/`GroupMedia`). تلاش مجدد: `retrySendMessage(MessageObject, boolean unsent, long payStars)` که با حالت `SEND_ERROR` از UI صدا زده می‌شود. |
| اتصال `MediaRepository` بدون شکستن ساختار؟ | با ساختن همان «پیام محلی» قبل از آپلود و سپس تأیید/خطا دادن همان پیام؛ هیچ تغییری در `FileLoader`/`MTProto` لازم نیست. |

## ۲) معماری Local-First پیاده‌شده

`platform/tl/PlatformLocalSend.java` (جدید) + `SendMessagesHelper.platformSendLocalMedia(...)`

جریان کار:

1. `platformSendLocalMedia(dialogId, path, kind, caption, replyTo)` پیام محلی می‌سازد
   (`send_state = SENDING`، `attachPath = path`، مدیا از `PlatformMediaSync.attachLocalMedia`).
2. پیام در `MessagesStorage` و در چت درج می‌شود (`updateInterfaceWithMessages` + `dialogsNeedReload`).
3. `putToSendingMessages` + `putToUploadingMessages` → حباب و وضعیت «در حال ارسال» نمایان است.
4. آپلود با `MediaRepository` (استریمی، پیشرفت، قابل لغو) روی همان فایل.
5. پیام در بک‌اند ساخته می‌شود (`client_message_id = local-<id>` برای حذف تکرار).
6. `platformConfirmLocalMedia(object, serverId)`: `SEND_STATE = SENT`، حذف از نقشه‌های ارسال،
   اطلاع به UI؛ فریم `message.new` بعدی با همان شناسه در `PlatformChatSync` dedupe می‌شود.
7. شکست → `platformFailLocalMedia`: `SEND_STATE = SEND_ERROR` + `messageSendError` → همان
   دکمهٔ «تلاش مجدد» موجود فعال می‌شود و `retrySendMessage` به `PlatformLocalSend.retry` می‌رسد.
8. لغو → `cancelSendingMessage` به `PlatformLocalSend.cancel` وصل شد (فلگ لغو + حذف placeholder).
9. تکرار: `PlatformMediaSync.isUploading(path)` از ساخت پیام دوم برای همان فایل جلوگیری می‌کند.

**وضعیت فعال‌سازی:** `BuildVars.LOCAL_FIRST_MEDIA = false` (پیش‌فرض خاموش). کد نوشته و از نظر
ایستا بررسی شده، اما تا زمانی که یک Build واقعی آن را تأیید نکند، مسیر قدیمی (آپلود سپس ساخت پیام)
فعال می‌ماند تا اپ کاربر نشکند. برای فعال‌سازی: `LOCAL_FIRST_MEDIA = true` پس از تأیید CI.

**آنچه با این معماری به‌دست می‌آید:** حباب قبل از پایان آپلود، وضعیت ارسال، دکمهٔ لغو موجود،
دکمهٔ «تلاش مجدد» موجود، و نمایش پیش‌نمایش از `attachPath`.
**آنچه باز هم تضمین‌شده نیست:** حلقهٔ درصد پیشرفت *داخل* حباب؛ تلگرام آن را از
`FileLoader`/`ImageLoader` خودش می‌گیرد و مسیر ما آن را دور می‌زند. پیشرفت اكنون در
`PlatformMediaSync.uploadProgress(path)` در دسترس است.

## ۳) Build واقعی Android

| بررسی | نتیجه |
|---|---|
| `java`/`JAVA_HOME` در سندباکس | موجود نیست (`java: command not found`؛ Gradle: «Please set the JAVA_HOME…») |
| Android SDK | نصب نیست (`ANDROID_HOME` خالی) |
| کش Gradle | خالی (۰ بایت وابستگی) |
| سازگاری نسخه‌ها | پروژه: `compileSdk 36`، `minSdk 23`، `targetSdk 36`، Java 8 source/target → **JDK 17 + `platforms;android-36` + `build-tools;36.0.0` در Workflow سازگار است** |
| اجرای Workflow با `gh workflow run` | **403: Resource not accessible by integration** (پرمیشن `Actions: write` وجود ندارد) |
| PR و اجرای CI با `pull_request` | trigger اضافه شد و PR #1 ساخته شد، اما `runs?event=pull_request` = **۰** |
| pushهای این جلسه | هیچ run‌ای نساختند (push با توکن GitHub App) |
| `GET /actions/permissions` | 403 (پرمیشن مدیریتی ندارد) |

**نتیجه: کامپایل واقعی انجام نشد. این مرحله از نظر Build ناقص است و علت آن دسترسی است، نه کد.**

**اقدام لازم از طرف مالک مخزن (هر یک کافی است):**
1. دادن پرمیشن **Actions: write** (و ترجیحاً Workflows: write) به GitHub App مربوط به Arena در
   `Settings → Integrations / GitHub Apps → Permissions`؛ یا
2. اجرای دستی: `Actions → Build Telegram APK → Run workflow` روی برنچ
   `arena/76249c66-telegram` با `mode = compile` (سریع) یا `full` (کامپایل + APK + اسموک)؛ یا
3. push برنچ با توکن شخصی خود مالک تا trigger `push` فعال شود.

## ۴) تست‌های اجراشده در این مرحله

- بک‌اند (بدون تغییر رفتاری در این مرحله): **۱۷۶ تست سبز**.
- Auditها: `check_android_phase1` ✅، `check_tl_contract` ✅ (شامل `TL_photoSize`/`TL_messageMediaPhoto`
  و متدهای `SendMessagesHelper`/`PlatformBridge`)، `check_dialogs_no_mtproto` ✅،
  `check_chat_no_mtproto` ✅ (۱۴ نقطهٔ ورود؛ `PlatformLocalSend` و `PlatformMediaSync` از نظر
  نمادهای ترنسپورت پاک‌اند).
- `javalang`: parse موفق روی `SendMessagesHelper.java` و `PlatformLocalSend.java` و
  `PlatformMediaSync.java`.
- پروب‌های زندهٔ API (فاز ۶.۱): `android_media_probe` ۲۱/۲۱، `android_chat_probe` ۱۲/۱۲،
  `android_contacts_probe` ۱۵/۱۵ (بدون تغییر بک‌اند در این مرحله).

## ۵) محدودیت‌های باقی‌مانده

1. **کامپایل/APK/Emulator انجام نشد** (دسترسی Actions؛ جزئیات بالا).
2. مسیر Local-First با فلگ خاموش است تا Build واقعی تأییدش کند.
3. پیشرفت درصدی داخل حباب و پخش ویدیو وصل نشده‌اند.
4. تست‌های «دو حساب، عکس/ویدیو/سند، قطع/وصل اینترنت، لغو/تلاش مجدد، بازکردن مجدد چت» در سطح
   API انجام شده‌اند (`android_media_probe`)، اما **در اپ واقعی نه** — زیرا APK ساخته نشد.
