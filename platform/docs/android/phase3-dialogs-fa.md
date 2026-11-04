# فاز ۳ — اتصال فهرست گفتگوها (DialogsActivity) به بک‌اند اختصاصی

تاریخ: ۲۰۲۶-۱۱-۰۴ · شاخه: `arena/76249c66-telegram` · وضعیت: **کد نوشته شد؛ کامپایل/APK/امولاتور انجام نشد.**

## ۱. مسیر دادهٔ فهرست در تلگرام (شناسایی‌شده)

```
DialogsActivity.loadDialogs(account)  →  MessagesController.loadDialogs(...)  →  MTProto/Storage
        ▲                                              │
        └──────── dialogsByFolder[folderId] ◄──────────┘   (SparseArray<ArrayList<TLRPC.Dialog>>)
DialogsActivity.getDialogsArray(...) → MessagesController.getDialogs(folderId) → dialogsByFolder
```

## ۲. کاری که انجام شد

| فایل | تغییر |
|---|---|
| `org/telegram/platform/tl/PlatformDialogsSync.java` | **جدید** — ساخت `TLRPC.TL_dialog`/`TL_user`/`TL_chat` از پاسخ API، نصب در `dialogsByFolder[0]` + `dialogs_dict` + `dialogsServerOnly`، مرتب‌سازی با `sortDialogsList`، اعلان `dialogsNeedReload`، و بارگذاری صفحه‌ای تا ۵۰۰ ردیف |
| `org/telegram/platform/repo/ChatRepository.java` | `dialogPage(limit, offset)` برای بارگذاری صفحه‌ای |
| `ui/DialogsActivity.java` | شاخهٔ محافظت‌شده در `loadDialogs`: اگر فلگ روشن باشد، منبع = بک‌اند اختصاصی؛ بقیهٔ کارها (تنظیمات، استیکرها) دست‌نخورده |
| `messenger/BuildVars.java` | `DIALOGS_FROM_PLATFORM = BuildConfig.DEBUG` (در Release خاموش) |
| `platform/backend/app/routers/ws.py` | رویدادهای `presence.updated` (اتصال/قطع) و `read.updated` |
| `platform/backend/app/services.py` | `presence_recipients()` — چه کسانی وضعیت آنلاین کاربر را می‌بینند |

داده‌های هر ردیف از API: نام/عنوان، نوع (private/group/channel/saved)، آخرین پیام و زمانش،
`unread_count`، `pinned`، `peer.is_online`، `last_seq` (برای خوانده‌شدن) و `member_count`.
مرتب‌سازی سنجاق‌شده‌ها در سرور انجام و در کلاینت با همان مقایسه‌گر خود تلگرام اعمال می‌شود.

## ۳. ارتباط زنده

`PlatformDialogsSync.onRealtimeEvent` روی `message.new`، `message.updated/edited`، `message.deleted`،
`presence.updated`، `read.updated`/`message.read` و `dialog.updated` فهرست را از API تازه می‌کند،
بنابراین ترتیب و شمارنده‌ها همیشه همان چیزی است که سرور می‌گوید (بدون حدس سمت کلاینت).

## ۴. آنچه هنوز وصل نشده (صادقانه)

1. **کامپایل نشده و روی دستگاه اجرا نشده** — نه JDK/SDK در این محیط هست، نه مجوز `actions:write` برای اجرای workflow.
2. **پیش‌نمایش متن آخرین پیام و آواتار** به مسیر `MessageObject`/`MessagesStorage` تلگرام وابسته است که در این فاز
   جایگزین نشده؛ ردیف‌ها با مدل‌های واقعی ساخته می‌شوند اما متن پیش‌نمایش ممکن است خالی بماند.
3. **تب‌های پوشه** هنوز فیلترهای خود تلگرام را نشان می‌دهند؛ API پوشه‌ها و `installFolder()` آماده است ولی
   ساخت `DialogFilter`های UI انجام نشده.
4. **منوهای تعاملی فهرست** (سنجاق/حذف/خوانده‌شدن از منوی نگه‌داشتن) هنوز به MTProto می‌روند؛ متدهای
   Repository آماده و تست‌شده‌اند اما سیم‌کشی دکمه‌ها انجام نشده.

## ۵. آزمایش‌ها

- **۱۲۴ تست بک‌اند سبز** (بدون کاهش؛ تست typing با احتساب رویداد جدید presence سخت‌گیرانه‌تر شد).
- `tools/android_demo_login_probe.py` روی سرور زنده: ۲۶ بررسی موفق، شامل دیدن فهرست واقعی هر دو حساب،
  آخرین پیام، شمارندهٔ خوانده‌نشده، وضعیت آنلاین/آفلاین و تحویل زندهٔ پیام روی WebSocket.
- `tools/check_tl_contract.py` و `tools/check_android_phase1.py` اجرا و سبز (فیلدهای TL و «فقط افزودن خط»).
