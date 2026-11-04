# فاز ۴ — اتصال ChatActivity به بک‌اند اختصاصی

شاخه: `arena/76249c66-telegram` · تاریخ: ۲۰۲۶-۱۱-۰۴

> **وضعیت فاز ۳:** پیاده‌سازی‌شده؛ **اعتبارسنجی نهایی APK باقی مانده** — تأییدشده اعلام نمی‌شود.

## ۱. تحلیل وابستگی (خلاصه — کامل در `phase4-plan-fa.md`)

`ChatActivity.java` = ۴۷٬۶۹۹ خط؛ `getMessagesController()` ۴۴۲ بار، `MessageObject` ۱٬۷۳۴ بار،
`SendMessagesHelper` ۱۳۶ بار، `ConnectionsManager` ۸۵ بار، `MessagesStorage` ۳۹ بار.

نقاط گلوگاه و جایگزین آن‌ها:

| عملیات | نقطهٔ اصلی | جایگزین در این فاز |
|---|---|---|
| تاریخچه | `MessagesController.loadMessages(...)` | ✔ `PlatformChatSync.loadHistory` |
| ارسال متن | `SendMessagesHelper.sendMessage(SendMessageParams)` | ✔ فقط متن خالص |
| حذف | `MessagesController.deleteMessages(...)` | ✔ به REST |
| خوانده‌شدن | `MessagesController.markDialogAsRead(...)` | ✔ از فاز ۳ |
| ویرایش | `SendMessagesHelper.editMessage(...)` | ✖ متد نوشته شد، **قلاب UI وصل نشد** |
| در حال نوشتن | `TL_messages_setTyping` | ✖ متد آماده، **قلاب UI وصل نشد** |
| جست‌وجو | `MessagesSearchAdapter` → `MessagesStorage` | ✖ فقط API و Repository |
| رسانه | `FileLoader`/CDN تلگرام | ✖ طبق دستور، به فاز رسانه موکول شد |

## ۲. APIهای جدید/تغییریافته

| مورد | تغییر |
|---|---|
| `GET /chats/{id}/messages?after_seq=` | صفحه‌بندی رو به جلو برای پر کردن شکاف پس از اتصال مجدد |
| `POST /chats/{id}/messages` | **idempotent**: اگر `client_message_id` تکراری باشد همان پیام قبلی برگردانده می‌شود و رویداد تازه‌ای منتشر نمی‌شود |
| `MessageRepository` (اندروید) | `loadHistoryAfter(...)`؛ مسیر حذف به `/messages/{id}` اصلاح شد (قبلاً مسیر اشتباه بود) |

بقیهٔ قابلیت‌ها از قبل در بک‌اند موجود بودند و اکنون استفاده می‌شوند: صفحه‌بندی `before_seq`،
ویرایش `PATCH /messages/{id}`، حذف `DELETE /messages/{id}`، پاسخ `reply_to_id`،
جست‌وجو `/messages/search`، typing، و رویدادهای `message.new/edited/deleted` + `read.updated`.

## ۳. تغییرات اندروید

| فایل | کار |
|---|---|
| `platform/tl/PlatformChatSync.java` (جدید) | تبدیل پیام‌های API به `MessageObject` و انتشار `NotificationCenter.messagesDidLoad` با همان امضای MTProto؛ صفحه‌بندی، ارسال، حذف، ویرایش، typing، همگام‌سازی پس از اتصال مجدد، حذف تکراری با مجموعهٔ شناسه‌ها |
| `messenger/MessagesController.java` | شاخهٔ محافظت‌شده در `loadMessages(...)` و `deleteMessages(...)` |
| `messenger/SendMessagesHelper.java` | شاخهٔ محافظت‌شده در `sendMessage(...)` فقط برای متن خالص (`isPlainText`) |
| `messenger/BuildVars.java` | `CHAT_FROM_PLATFORM = BuildConfig.DEBUG` |
| `platform/PlatformBridge.java` | رویدادهای گفتگو به `PlatformChatSync` و همگام‌سازی پس از اتصال مجدد |
| `platform/repo/MessageRepository.java` | `loadHistoryAfter` + اصلاح مسیر حذف |

ظاهر، چیدمان، انیمیشن و منوهای ChatActivity **تغییر نکردند**؛ فقط منبع داده عوض شد و
ChatActivity همان اعلان همیشگی را می‌گیرد.

## ۴. تست‌ها

| مورد | نتیجه |
|---|---|
| `tests/test_chat_phase4.py` (جدید، ۷ تست) | صفحه‌بندی دوطرفه، پاسخ با والد و فرستنده، ویرایش/حذف + رویدادها، typing، جست‌وجو، ارسال تکراری با `client_message_id` یکسان، وضعیت خوانده‌شدن per-member |
| کل بک‌اند | **۱۴۱ سبز** (۱۳۴ قبلی + ۷ جدید) |
| `check_android_phase1.py` | OK — ۲۵ اندپوینت، بدون وابستگی MTProto بیرون از seam |
| `check_tl_contract.py` | OK |
| `check_dialogs_no_mtproto.py` | OK |

## ۵. آنچه تأیید نشده / باقی‌مانده
1. **کامپایل، APK و امولاتور انجام نشد** (نه `javac`، نه Android SDK، شبکهٔ Maven/Google بسته).
   بنابراین هیچ‌یک از رفتارهای داخل اپ (نمایش حباب‌ها، ارسال، پاسخ، ویرایش، حذف) **تأییدشده نیست**.
2. **ویرایش و typing در UI وصل نشده‌اند** — متدهای Repository و `PlatformChatSync` آماده‌اند.
3. **جست‌وجوی داخل گفتگو در UI وصل نشده** — اندپوینت و Repository آماده و تست‌شده‌اند.
4. **رسانه** (عکس/فایل/ویس) عمداً مهاجرت نکرده؛ ارسال رسانه همچنان مسیر اصلی تلگرام را می‌رود.
5. نگاشت `MessageObject` ممکن است برای برخی انواع پیام (سرویس/فروم/تاپیک) ناقص باشد.
