# فاز ۴ — اتصال ChatActivity به بک‌اند اختصاصی (تحلیل و برنامه)

تاریخ: ۲۰۲۶-۱۱-۰۴ · شاخه: `arena/76249c66-telegram`

> **وضعیت فاز ۳:** پیاده‌سازی‌شده؛ **اعتبارسنجی نهایی APK باقی مانده** و تأییدشده اعلام نمی‌شود.

## ۱. تحلیل وابستگی ChatActivity

`ui/ChatActivity.java` = **۴۷٬۶۹۹ خط**. شمارش وابستگی‌ها (grep روی همان فایل):

| نماد | تعداد |
|---|---|
| `getMessagesController()` | ۴۴۲ |
| `SendMessagesHelper` | ۱۰۰ (+۳۶ `getSendMessagesHelper()`) |
| `ConnectionsManager` | ۸۵ |
| `MessagesStorage` | ۳۹ |
| `MessageObject` | ۱٬۷۳۴ |

### نقاط گلوگاه (چیزی که واقعاً باید جایگزین شود)

| # | عملیات | نقطهٔ فعلی | مسیر MTProto |
|---|---|---|---|
| ۱ | تاریخچه | `MessagesController.loadMessages(dialogId, mergeDialogId, loadInfo, count, max_id, offset_date, …, classGuid, load_type, last_message_id, mode, …)` — ۱۹ فراخوانی از ChatActivity | `TL_messages_getHistory` |
| ۲ | ارسال | `SendMessagesHelper.sendMessage(SendMessageParams)` | `TL_messages_sendMessage` |
| ۳ | ویرایش | `SendMessagesHelper.editMessage(MessageObject, …)` | `TL_messages_editMessage` |
| ۴ | حذف | `MessagesController.deleteMessages(ArrayList<Integer>, …)` | `TL_messages_deleteMessages` |
| ۵ | خوانده‌شدن | `MessagesController.markDialogAsRead(…)` — **فاز ۳ وصل شد** | `TL_messages_readHistory` |
| ۶ | در حال نوشتن | `SendMessagesHelper.sendTyping?` / `TL_messages_setTyping` | MTProto |
| ۷ | جست‌وجو | `MessagesSearchAdapter` → `MessagesStorage` → `TL_messages_search` | MTProto |
| ۸ | نمایش پیام‌های جدید | `MessagesController` updates handler (`TL_updates`) | MTProto |
| ۹ | وضعیت تحویل/خوانده‌شدن | `TL_updateReadHistoryInbox/Outbox` | MTProto |
| ۱۰ | رسانه | `FileLoader`/`SendMessagesHelper` | MTProto + CDN تلگرام |

### راه اتصال بدون دست‌زدن به UI
ChatActivity پیام‌ها را از `NotificationCenter.messagesDidLoad` می‌گیرد:
`(dialogId, count, ArrayList<MessageObject> messages, isCache, …, classGuid, loadIndex, …, mode)` (آرگ ۱۰ = guid، ۱۱ = loadIndex، ۱۴ = mode).
اگر همان اعلان با همان امضا از دادهٔ بک‌اند پر شود، **آداپتر و چیدمان و انیمیشن‌ها بدون هیچ تغییری** کار می‌کنند.
`ChatActivity.messages` نیز `public ArrayList<MessageObject>` است، پس نصب مستقیم ممکن است.

## ۲. برنامهٔ اجرایی (به ترتیب)

| گام | کار | خروجی |
|---|---|---|
| ۰ | تحلیل وابستگی (همین سند) | ✔ |
| ۱ | بک‌اند: صفحه‌بندی تاریخچه (`before_seq`)، همگام‌سازی (`since_seq`)، جست‌وجو (`/messages/search`)، تضمین یکتایی پیام | API + تست |
| ۲ | Repository اندروید: history/send/edit/delete/reply/search/sync + dedupe + outbox | `MessageRepository` |
| ۳ | آداپتور `PlatformChatSync`: تبدیل پیام‌های API به `MessageObject` و پست `messagesDidLoad`، هندل `message.new/updated/deleted`، typing، sync پس از اتصال مجدد | کلاس جدید |
| ۴ | گلوگاه‌ها: `MessagesController.loadMessages`، `SendMessagesHelper.sendMessage`، `MessagesController.deleteMessages`، ویرایش | ۴ شاخهٔ محافظت‌شده |
| ۵ | وضعیت تحویل/خوانده‌شدن | از `last_read_seq` + `message.read` |
| ۶ | معماری رسانه (بدون پیاده‌سازی کامل): `file_id` + آپلود `/files` + `kind` | طراحی + وصل نشده |
| ۷ | تست بک‌اند + audit ایستا + مستند | ✔ |

## ۳. نگاشت به ۱۳ خواستهٔ فاز ۴

۱ تاریخچه صفحه‌بندی‌شده ✔ · ۲ ارسال/دریافت متن ✔ · ۳ فرستنده و زمان ✔ (از `MessageObject`) ·
۴ وضعیت ارسال/دریافت/خوانده‌شدن ✔ · ۵ ویرایش/حذف ✔ · ۶ پاسخ/نقل‌قول ✔ (`reply_to_id`) ·
۷ مخاطب و گروه ✔ · ۸ پیام جدید با WebSocket ✔ · ۹ در حال نوشتن ✔ · ۱۰ همگام‌سازی پس از قطع ✔ ·
۱۱ جلوگیری از تکرار ✔ (`event_id` + نگاشت شناسه) · ۱۲ جست‌وجو در گفتگو ✔ (API) ·
۱۳ رسانه: فقط معماری، **پیاده‌سازی به فاز بعد**

## ۴. ریسک‌ها و آنچه تأیید نشده
- **کامپایل/APK/امولاتور در این محیط ممکن نیست** (نه `javac`، نه Android SDK، شبکهٔ Maven/Google بسته).
  پس هیچ‌یک از موارد «داخل اپ» تأییدشده نیست و فقط قرارداد داده و ترتیب فراخوانی‌ها اثبات می‌شود.
- ChatActivity به `MessageObject` با فیلدهای زیاد وابسته است؛ نگاشت ناقص می‌تواند در بعضی سل‌ها رفتار
  ظاهری را تغییر دهد (بدون تغییر چیدمان، ولی محتوای متفاوت).
- رسانه (عکس/فایل) تا فاز مربوطه از MTProto می‌آید؛ در این فاز فقط متن/پاسخ/ویرایش/حذف مهاجرت می‌کند.

## ۵. قواعد رعایت‌شده
- هیچ تغییری در ظاهر ChatActivity، انیمیشن‌ها و منوها داده نمی‌شود؛ فقط منبع داده.
- هیچ داده‌ی Mock برای نمایش موفقیت ساخته نمی‌شود.
- منطق موجود تلگرام حذف نمی‌شود؛ شاخه‌های پلتفرم فقط در حالت `BuildConfig.DEBUG` فعال‌اند.
