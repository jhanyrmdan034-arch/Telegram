# فاز ۴ — مرحلهٔ تکمیلی (ویرایش، Typing، جست‌وجو و اعتبارسنجی)

شاخه: `arena/76249c66-telegram` · تاریخ: ۲۰۲۶-۱۱-۰۴

## ۱. قابلیت‌هایی که در این مرحله به UI وصل شدند

| # | قابلیت | نقطهٔ اتصال | روش |
|---|---|---|---|
| ۱ | **ویرایش پیام** | `SendMessagesHelper.editMessage(MessageObject, String, …)` | شاخهٔ پلتفرم → `PATCH /messages/{id}`؛ پاسخ از رویداد `message.edited` می‌آید |
| ۲ | **Typing (ارسال)** | `MessagesController.sendTyping(long, long, int, int)` | شاخهٔ پلتفرم → `POST /chats/{id}/typing` |
| ۲ | **Typing (نمایش)** | `MessagesController.platformSetTyping(…)` (متد جدید) | نوشتن در همان `printingStrings`/`printingStringsTypes` که آپدیت‌های MTProto پر می‌کنند + `NotificationCenter.updateInterfaces` |
| ۳ | **جست‌وجوی داخل گفتگو** | `MediaDataController.searchMessagesInChat(…)` | `GET /chats/{id}/messages/search` و انتشار همان `chatSearchResultsAvailable` / `chatSearchResultsLoading` |
| ۴ | **پاسخ به پیام** | `reply_to_id` در API | کلاینت `replyToMsg` را به UUID والد نگاشت می‌کند؛ والد در تاریخچه واقعی است |

## ۲. اعتبارسنجی وضعیت‌ها (۵ تا ۸)

`tools/android_chat_probe.py` روی سرور زنده با `demo_alice` و `demo_bob` — **۱۲ بررسی، همه موفق**:

1. `message.new` روی WebSocket واقعی به گیرنده می‌رسد
2. رویداد `typing` با `chat_id` و `user_id` درست
3. شمارندهٔ خوانده‌نشده برای گیرنده دیده می‌شود (تحویل)
4. `read.updated` به فرستنده می‌رسد (خوانده‌شدن)
5. ویرایش ذخیره می‌شود و `edited_at` دارد
6. `message.edited` به طرف مقابل می‌رسد
7. پاسخ، والد خود را نگه می‌دارد
8. حذف، پیام را برای **گیرنده** هم حذف می‌کند
9. `message.deleted` منتشر می‌شود
10. ارسال تکراری با همان `client_message_id` پیام دوم نمی‌سازد
11. جست‌وجوی داخل گفتگو پیام نشانه‌دار را پیدا می‌کند
12. پیام‌های ارسال‌شده در قطعی، با `after_seq` **دقیقاً یک‌بار** بازیابی می‌شوند

> این‌ها **تست API/پروتکل** هستند، نه تست اپلیکیشن اندروید. ادعای تست درون‌برنامه‌ای نیست.

## ۳. تضمین عدم استفاده از مسیر تلگرام (بند ۱۰)

`tools/check_chat_no_mtproto.py` (جدید) برای **۱۰ نقطهٔ ورود** بررسی می‌کند که شاخهٔ پلتفرم
**قبل از** اولین نشانهٔ MTProto (`sendRequest`، `ConnectionsManager`، `MessagesStorage`، `TLRPC.TL_messages_`) باشد:
فهرست گفتگو، تاریخچه، ارسال، ویرایش، حذف، typing، رسید خوانده‌شدن، جست‌وجوی گفتگو، سنجاق، آرشیو.
خروجی: `OK: 10 chat/dialog entry points branch out before MTProto`.
همچنین `platform/tl/*` هیچ نماد MTProto ندارد.

## ۴. وابستگی‌های باقی‌ماندهٔ ChatActivity (بند ۹)

`ui/ChatActivity.java` = ۴۷٬۶۹۹ خط. شمارش: `getMessagesController()` **۴۴۲**، `MessageObject` **۱٬۷۳۴**،
`SendMessagesHelper` **۱۳۶**، `ConnectionsManager` **۸۵**، `MessagesStorage` **۳۹**.

| دسته | وضعیت | توضیح |
|---|---|---|
| خواندن آبجکت‌های کش‌شده (کاربر/چت/دیالوگ) | ✔ از داده‌های ما پر می‌شود | خودش درخواست شبکه نیست |
| تاریخچه، ارسال متن، ویرایش، حذف، خوانده‌شدن، typing، جست‌وجو، پاسخ | ✔ مهاجرت‌شده | ۱۰ نقطهٔ ورود بالا |
| **رسانه** (عکس/ویدئو/فایل/ویس/استیکر/GIF) | ✖ مهاجرت نشده | آپلود و CDN تلگرام؛ طبق دستور به فاز رسانه موکول شد |
| **نظرسنجی، مکان، مخاطب، تماس** | ✖ | `TL_messages_sendMedia` و مشابه |
| **فوروارد رسانه، پیام زمان‌بندی‌شده** | ✖ | مسیر `SendMessagesHelper` رسانه‌ای |
| **چت مخفی، ربات‌ها، واکنش‌ها، ترجمه** | ✖ | خارج از دامنهٔ فاز ۴ |
| **تاپیک/فروم، پاک‌کردن تاریخچه، حذف کامل گفتگو** | ✖ | نیازمند اندپوینت اختصاصی |
| **حافظهٔ محلی (`MessagesStorage`, ۳۹ مورد)** | ✖ | کش محلی تلگرام هنوز استفاده می‌شود (فقط خواندن، بدون شبکه) |
| **`ConnectionsManager` (۸۵ مورد)** | ✖ | درخواست‌های خام باقی‌مانده (poll/theme/…) |

## ۵. آنچه تأیید نشده
1. **کامپایل/APK/امولاتور انجام نشد** (نه `javac`، نه Android SDK، شبکهٔ Maven/Google بسته) →
   نمایش typing، زیرنویس، هایلایت جست‌وجو و ویرایش در حباب **در اپ تأیید نشده**.
2. ماسک نتایج جست‌وجو در `chatSearchResultsAvailable` همیشه `0` ارسال می‌شود (همهٔ انواع)؛ فیلتر نوع رسانه پیاده نشده.
3. `PlatformChatSync` برای انواع خاص پیام (سرویس/تاپیک) نگاشت کامل ندارد.
4. `read.updated` علاوه بر `message.read` منتشر می‌شود؛ مصرف‌کنندهٔ قدیمی تغییری نمی‌بیند.
