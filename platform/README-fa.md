# پلتفرم پیام‌رسان مستقل — بک‌اند، پنل مدیریت و زیرساخت

این پوشه، زیرساخت اختصاصی جایگزین تلگرام است: API، دیتابیس، پنل مدیریت وب و استقرار.

> **وضعیت:** بک‌اند و پنل مدیریت کامل و تست‌شده‌اند (۷۸ تست روی PostgreSQL واقعی).
> **اپ اندروید هنوز به این API وصل نشده است** — برنامه در `docs/ANDROID-INTEGRATION-fa.md`.
> گزارش دقیق و بی‌اغراق: `docs/STATUS-fa.md`.

---

## اجرای سریع

### با Docker (توصیه‌شده برای سرور)

```bash
cd deploy
cp .env.example .env      # همهٔ CHANGE_ME را پر کنید
docker compose up -d --build
docker compose exec api python -m app.cli create-superuser --username admin --password 'StrongPass!23'
```

سپس:
- پنل مدیریت: `https://<your-domain>/admin`
- مستندات API: `https://<your-domain>/docs`
- سلامت: `https://<your-domain>/api/v1/readyz`

### بدون Docker (توسعه)

```bash
python3 -m venv ../../.venv
../../.venv/bin/pip install -r backend/requirements-dev.txt

export DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/messenger"
export JWT_SECRET="$(openssl rand -hex 32)"
export REDIS_URL="redis://localhost:6379/0"

cd backend
../../.venv/bin/alembic upgrade head
../../.venv/bin/python -m app.cli create-superuser --username admin --password 'StrongPass!23'
../../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## ساختار

```
platform/
├── backend/            FastAPI + SQLAlchemy + Alembic (منبع حقیقت: PostgreSQL)
│   ├── app/
│   │   ├── main.py         ساخت اپ، middleware، هندلر خطا
│   │   ├── models.py       ۲۰ جدول
│   │   ├── security.py     Argon2، JWT، refresh rotation
│   │   ├── realtime.py     WebSocket + گذرگاه رویداد
│   │   ├── permissions.py  ۱۷ مجوز، ۶ نقش سیستمی
│   │   └── routers/        auth, users, contacts, chats, messages, files, calls, support, admin, ws, health
│   ├── alembic/        مهاجرت‌های برگشت‌پذیر
│   └── tests/          ۷۸ تست (pytest) روی PostgreSQL واقعی
├── admin/              پنل مدیریت وب (بدون هیچ وابستگی خارجی)
├── deploy/             docker compose، Nginx+TLS، coturn، backup/restore، deploy.sh
├── tests/load/         تست بار واقعی
├── scripts/            خروجی OpenAPI
└── docs/               معماری، وضعیت، نقشهٔ راه اندروید، openapi.json
```

---

## قابلیت‌های آماده (تست‌شده)

- **احراز هویت:** ثبت‌نام/ورود با رمز، Argon2id، JWT کوتاه‌عمر، refresh rotation با تشخیص سرقت، مدیریت نشست و دستگاه
- **پیام‌رسانی:** چت خصوصی، گروه، کانال، Saved Messages؛ ترتیب دقیق پیام زیر هم‌زمانی؛ pagination؛ ویرایش/حذف؛ read receipt؛ تایپینگ
- **بلادرنگ:** WebSocket با fan-out قابل مقیاس (Redis pub/sub)
- **فایل:** آپلود content-addressed، کنترل دسترسی مبتنی بر عضویت در چت، محدودیت حجم
- **تماس:** سیگنالینگ کامل + ICE/TURN (رسانه با coturn)
- **پشتیبانی:** گزارش تخلف و تیکت با thread
- **مدیریت:** RBAC، آمار، moderation، نقش‌های سفارشی، تنظیمات، حالت نگهداری، audit، رویدادهای امنیتی، خروجی CSV/PDF
- **زیرساخت:** عملیات امن‌شده، Nginx، TLS، backup رمزنگاری‌شده، بازگردانی، استقرار خودکار

## آنچه هنوز نیست

- اتصال اپ اندروید (نقشهٔ راه موجود)
- رمزنگاری سرتاسری برای چت محرمانه
- Push notification
- S3/MinIO و پردازش رسانه
- تست روی سرور واقعی با دامنه

فهرست کامل با شاهد: `docs/STATUS-fa.md`.
