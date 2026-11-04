# اتصال اپ اندروید موجود به بک‌اند اختصاصی — ارزیابی و نقشهٔ راه

> **وضعیت: شروع نشده.** این سند یک تحلیل فنی و نقشهٔ اجرایی است، نه گزارش پیشرفت.
> در این جلسه، بک‌اند و پنل مدیریت ساخته و تست شدند؛ تغییر کلاینت اندروید انجام نشده است.

---

## ۱. واقعیت فنی کلاینت فعلی

سورس موجود، اپ رسمی تلگرام است و داده‌هایش را از طریق **MTProto** روی TCP خام می‌گیرد، نه HTTP:

| لایه | حجم/تعداد | نکتهٔ کلیدی برای جایگزینی |
|---|---|---|
| `org.telegram.tgnet` | ~۷۵ هزار خط (`TLRPC.java` تنها ۷۱٬۵۵۷ خط) | ۲٬۰۸۸ کلاس TL؛ کلاینت **۱٬۴۶۸** نوع/متد متمایز را استفاده می‌کند |
| `ConnectionsManager` | ۱٬۵۶۸ خط | نقطهٔ ورود همهٔ درخواست‌ها + مدیریت اتصال، نمک، DC و … |
| پشتهٔ بومی | `jni/tgnet`, `jni/td` | پیاده‌سازی MTProto در C++ |
| کنترلرها | ۳۰+ کلاس `*Controller`/`*Storage` | `MessagesController` ۲۵٬۶۴۱ خط، `MessagesStorage` ۱۹٬۰۶۴ خط |
| صفحات | ۲۱۵ اکتیویتی/فرگمنت در `org.telegram.ui` | همه به مدل‌های `TLRPC` و `MessageObject` وابسته‌اند |

نتیجهٔ مهم: رابط کاربری، «رندرِ مدل‌های TL» است. تا وقتی مدل‌ها به همان شکل پر شوند، UI نیاز به تغییر ندارد.

---

## ۲. سه مسیر ممکن

| مسیر | توضیح | مزیت | هزینه/ریسک |
|---|---|---|---|
| **A. سرور سازگار با MTProto** | پیاده‌سازی سمت سرور MTProto و زیرمجموعه‌ای از ~۱٬۴۶۸ متد / ۲٬۰۸۸ کلاس | صفر تغییر در کلاینت | بسیار بزرگ؛ شبیه‌سازی API تلگرام، ریسک حقوقی/سردرگمی برند؛ نگهداری نسخه‌های آیندهٔ کلاینت |
| **B. آداپتور ترابری در کلاینت** | نگه‌داشتن UI و کنترلرها، جایگزینی `ConnectionsManager` + بارگذاری داده با کلاینت REST/WS که همان `TLRPC.*` را می‌سازد | UI دست‌نخورده، قابل انجام تدریجی و feature-flag | تماس با `SendMessagesHelper`، `MessagesStorage` (sync/کش) و مسیر آپلود؛ چند ماه کار مهندسی |
| **C. بازنویسی کلاینت** | استفاده از منابع UI (layout/theme) در یک اپ جدید | تمیزی معماری | همهٔ ۲۱۵ صفحه + انیمیشن‌ها از دست می‌رود؛ مغایر خواستهٔ «حفظ کامل رابط کاربری» |

**توصیه: مسیر B**، چون تنها مسیری است که هم رابط را حفظ می‌کند و هم به شبیه‌سازی پروتکل تلگرام (مسیر A) نمی‌انجامد.

---

## ۳. نقشهٔ اتصال: هر قابلیت UI ← کدام endpoint

| صفحه/قابلیت در کلاینت | منبع دادهٔ فعلی | جایگزین در این پلتفرم |
|---|---|---|
| LoginActivity / ثبت‌نام | `TL_auth_signIn` + کد پیامکی | `POST /auth/login` · `POST /auth/register` (بدون پیامک) |
| لیست گفتگوها (DialogsActivity) | `TL_messages_getDialogs` | `GET /chats` |
| تاریخچهٔ چت (ChatActivity) | `TL_messages_getHistory` | `GET /chats/{id}/messages?before_seq=` |
| ارسال پیام | `TL_messages_sendMessage` | `POST /chats/{id}/messages` |
| ویرایش/حذف | `TL_messages_editMessage` / `deleteMessages` | `PATCH`/`DELETE /messages/{id}` |
| تیک خوانده‌شدن | `TL_messages_readHistory` | `POST /chats/{id}/read` |
| «در حال تایپ» | `TL_messages_setTyping` | `POST /chats/{id}/typing` یا WS `typing` |
| آپلود/دانلود فایل | `FileUploadOperation` + DC اختصاصی | `POST /files` · `GET /files/{id}/content` |
| پروفایل | `TL_users_getFullUser` | `GET /users/{id}` · `PATCH /users/me` |
| مخاطبین | `TL_contacts_*` | `GET/POST/DELETE /contacts` |
| ساخت گروه/کانال | `TL_messages_createChat` / `channels_createChannel` | `POST /chats` با `type=group|channel` |
| مدیریت اعضا/نقش‌ها | `TL_channels_editAdmin`, `addChatUser` | `POST/PATCH/DELETE /chats/{id}/members` |
| تنظیمات (بخش عمومی) | `TL_account_update*` + `SharedPreferences` | `PATCH /users/me` با `settings` |
| دستگاه‌ها/نشست‌ها | `TL_account_getAuthorizations` | `GET /auth/sessions` · `DELETE /auth/sessions/{id}` |
| تماس صوتی/تصویری | `TL_phone_*` + tgcalls | `POST /chats/{id}/calls` + WS `call.*` + ICE از `/calls/ice` |
| گزارش تخلف | `TL_account_reportPeer` | `POST /reports` |
| پشتیبانی | — (تلگرام: @smstelegram) | `POST /tickets` |

---

## ۴. قابلیت‌هایی که بک‌اند معادلی ندارند (باید پنهان یا جایگزین شوند)

اینها در UI وجود دارند و بدون تصمیم صریح، «دکمهٔ بی‌عمل» می‌شوند — که طبق قواعد پروژه ممنوع است:

| قابلیت | تصمیم پیشنهادی |
|---|---|
| Premium / Stars / Gifts / Billing | حذف از UI (فاز ۱) یا پیاده‌سازی اشتراک داخلی (فاز بعد) |
| Wallet / TON | حذف از UI |
| Bots و Bot API | حذف در فاز ۱؛ در فاز ۴ می‌توان سرویس بات نوشت |
| Stories | پیاده‌سازی در فاز ۳ (نیازمند جدول و TTL) یا پنهان‌سازی |
| Secret Chats (E2EE) | پنهان تا پیاده‌سازی پروتکل (فاز ۴) — نه اینکه ادعای E2EE شود و نباشد |
| Boost / Monetization / Affiliate | حذف |
| Passkeys / SafetyNet / Play Integrity | حذف یا جایگزینی با WebAuthn محلی |
| ترجمهٔ پیام | با MLKit محلی کار می‌کند؛ وابسته به بک‌اند نیست |
| نقشه/Geo | `meta` پیام از نوع `location` آماده است؛ سرویس نقشه باید خودی شود |

---

## ۵. گام‌های اجرایی پیشنهادی (فازبندی‌شده)

**فاز ۰ — آماده‌سازی (۱–۲ هفته)**
1. ماژول `platform-client` در پروژهٔ اندروید: Retrofit/OkHttp + WebSocket + ذخیرهٔ امن توکن (EncryptedSharedPreferences).
2. تعریف نقشهٔ «TL → DTO اختصاصی» و لایهٔ mapper که `TLRPC.TL_message`, `TL_user`, `TL_chat` می‌سازد.
3. Feature flag (`BuildVars.USE_CUSTOM_BACKEND`) تا هر دو مسیر قابل انتخاب باشد.

**فاز ۱ — ورود و گفتگو (۳–۵ هفته)**
4. جایگزینی `LoginActivity` data path با `/auth/*` (بدون پیامک) + صفحهٔ ثبت‌نام موجود.
5. `DialogsActivity`: بارگذاری `GET /chats` در `MessagesController.loadDialogs`.
6. `ChatActivity`: تاریخچه، ارسال، ویرایش، حذف، read/typing از REST + WS.
7. جایگزینی sync با `MessagesStorage` (کش محلی فعلی می‌ماند، فقط منبع تغذیه عوض می‌شود).

**فاز ۲ — رسانه و گروه‌ها (۴–۶ هفته)**
8. `FileUploadOperation` → `POST /files` با multipart و پیشرفت.
9. آواتار، گالری، پخش‌کننده (بدون تغییر UI).
10. گروه/کانال و مدیریت اعضا و مجوزها.

**فاز ۳ — تماس و اعلان (۳–۴ هفته)**
11. سیگنالینگ تماس روی WS + coturn برای رسانه (جایگزین tgcalls در فاز اول با یک پیاده‌سازی WebRTC سبک).
12. Push: سرویس FCM خودی یا WebSocket در پس‌زمینه.

**فاز ۴ — رمزنگاری سرتاسری (۴+ هفته)**
13. X3DH + Double Ratchet (کتابخانهٔ بالغ، مثل libsignal) برای «چت‌های محرمانه»؛ ذخیرهٔ سرور فقط ciphertext.
14. ممیزی مستقل امنیتی — بدون آن، نباید E2EE نامیده شود.

**فاز ۵ — پاک‌سازی**
15. حذف/پنهان‌سازی قابلیت‌های بدون بک‌اند (بند ۴) و تست دودی تمام صفحات.

---

## ۶. برآورد صادقانه

- این کار **در یک جلسه ممکن نیست**؛ حتی مسیر B چند صد ساعت مهندسی و تست دستگاه واقعی می‌خواهد.
- آنچه امروز آماده است، همان چیزی است که کلاینت به آن وصل می‌شود: ۸۲ endpoint تست‌شده، WebSocket بلادرنگ، RBAC، پنل مدیریت و زیرساخت استقرار.
- پیشنهاد: شروع فاز ۰ و ۱ روی همین شاخه، با این قید که هر صفحه فقط وقتی «متصل» اعلام شود که روی دستگاه واقعی تست شده باشد.
