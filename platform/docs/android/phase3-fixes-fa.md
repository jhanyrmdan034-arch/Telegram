# فاز ۳ — رفع اشکال‌های باقی‌مانده

شاخه: `arena/76249c66-telegram` · وضعیت: کد نوشته و بررسی‌های ایستا سبز؛ **کامپایل/APK انجام نشد.**

## ۱. پیش‌نمایش آخرین پیام
منبع واقعی پیش‌نمایش در تلگرام `MessagesController.dialogMessage` است (نه `MessagesStorage`)؛
`DialogCell.update()` از همان `ArrayList<MessageObject>` اولین پیام را می‌خواند. بنابراین
`PlatformDialogsSync` اکنون برای هر ردیف:
`TLRPC.TL_message` ← `DtoMapper.toMessage` ← `MessageObject(account, msg, false, false)` ← `dialogMessage.put(dialogId, [msg])`
و همان `top_message`/`last_message_date` را ست می‌کند؛ پس متن، فرستنده و زمان از همان مسیر خودِ اپ می‌آید.

## ۲. آواتار
`PlatformAvatars`: دانلود `GET /api/v1/files/{avatar_file_id}/content` با هدر توکن، کش دیسکی
(`cacheDir/platform_avatars/{fileId}.img` + sidecar برای ETag → درخواست بعدی ۳۰۴)، رمزگشایی بیت‌مپ،
و تحویل به همان `ImageReceiver` ردیف (`setImageBitmap`) با اعلان `dialogsNeedReload`.
حکاکی/اندازه/حاشیهٔ آواتار دست‌نخورده است. کلید کش = شناسهٔ فایل، پس آپلود جدید = فایل جدید = بی‌اعتبار شدن خودکار.
اتصال: دو قلاب کوچک در `DialogCell` بعد از `setForUserOrChat(...)`.

## ۳. پوشه‌ها
`GET /api/v1/folders` → برای هر پوشه یک `MessagesController.DialogFilter` (نام، ترتیب، `alwaysShow`)
و لیست دیالوگ‌های آن پوشه در `dialogsByFolder[i]`. تب‌ها همان تب‌های بومی تلگرام‌اند؛ فقط داده عوض شده.

## ۴. منوهای گفتگو
`PlatformDialogActions` + `ChatRepository.pinBlocking/markReadBlocking/archiveBlocking/deleteBlocking`.
`DialogsActivity.pinDialog` یک شاخهٔ محافظت‌شده گرفت: سنجاق/برداشتن سنجاق از Repository.
آرشیو در بک‌اند اضافه شد (`chat_members.archived`، `POST /chats/{id}/archive`، پارامتر `?archived=`).
حذف واقعی طبق سیاست موجود فقط برای گروه/کانالِ owner مجاز است؛ چت خصوصی ۴۰۰ می‌دهد و منو برای آن آرشیو پیشنهاد می‌کند.

## ۵. باز کردن گفتگو
`dialogId` نگاشت‌شده در دیالوگ/پیر نصب می‌شود و `PlatformDialogActions` قبل از هر درخواست،
شناسهٔ عددی را با `IdMapper.toUuid` به UUID برمی‌گرداند؛ اگر نگاشت نباشد هیچ درخواستی ارسال نمی‌شود.
ساخت چت تکراری سمت سرور جلوگیری می‌شود (تست paging این را تأیید می‌کند).

## ۶. ارتباط زنده
هر فریم WebSocket اکنون `event_id` دارد؛ `RealtimeClient` آن را در یک مجموعهٔ حداکثر ۱۰۲۴تایی نگه می‌دارد
و تکراری‌ها را دور می‌ریزد. پس از اتصال مجدد، `PlatformDialogsSync.refresh()` اجرا می‌شود تا فریم‌های ازدست‌رفته جبران شوند.
`presence.updated` (اتصال/قطع) و `dialog.updated` (سنجاق/آرشیو) هم در بک‌اند اضافه شدند.

## ۷. تست‌ها
- `tests/test_dialogs_phase3.py` — **۹ تست جدید** (پیش‌نمایش، سنجاق و ترتیب، شمارندهٔ خوانده‌نشده، آرشیو، حذف، پوشه‌ها، صفحه‌بندی بدون هم‌پوشانی، یکتایی `event_id`، عدم نشت آرشیو به لیست اصلی).
- **مجموع: ۱۳۳ تست سبز** (۱۲۴ قبلی دست‌نخورده).
- `check_android_phase1.py` و `check_tl_contract.py` سبز؛ `docs/openapi.json` بازتولید شد (۹۷ عملیات).
- Migration جدید: `16d5dcda8e53` (archived).

## ۸. محدودیت‌های باقی‌مانده (صادقانه)
1. **کامپایل/APK/امولاتور انجام نشد** (JDK/SDK و مجوز Actions در دسترس نیست) → رندر واقعی ردیف‌ها، پیش‌نمایش و آواتار **تأیید نشده**.
2. `out` (پیام من) در پیش‌نمایش همیشه `false` است؛ پیشوند «You:» ممکن است نمایش داده نشود.
3. پوشه‌ها فقط چت‌های گروه/کانال را در بر می‌گیرند (بک‌اند برای پوشه‌ها `chat_ids` نگه می‌دارد، نه user_ids).
4. «علامت‌گذاری خوانده‌شده/آرشیو/حذف» در سطح Repository و API آماده و تست‌شده‌اند، اما فقط **سنجاق** در UI سیم‌کشی شده؛ بقیهٔ آیتم‌های منو همچنان MTProto هستند.
5. باز کردن گفتگو ساختار درست را می‌دهد، اما محتوای ChatActivity تا فاز ۴ از MTProto می‌آید.
