# Yangi tashkilot uchun bo'sh nusxa — o'rnatish yo'riqnomasi

Har tashkilot — **o'z serveri, o'z bazasi, o'z Telegram boti, o'z admini**. Mavjud
tashkilotning `db.sqlite3`, `telegram.json`, `.env` fayllari HECH QACHON berilmaydi.
Kod bitta (GitHub) — yangilanishlar hamma nusxaga `git pull` bilan tushadi.

Vaqt: ~1 soat (server + bot), keyin tashkilot bilan 1–2 soat boshlang'ich kiritish.

## 0. Tayyorgarlik (tashkilot beradi)
- Server akkaunti: PythonAnywhere (Hacker tarifi tavsiya) yoki VPS. Sayt manzili.
- Telegram bot: @BotFather → `/newbot` → **token**. Admin o'z chatidan botga `/start`
  yozadi (chat_id keyin `telegram_chatid` buyrug'i bilan olinadi).
- Asosiy admin uchun kuchli parol.

## 1. Kod
```bash
git clone https://github.com/gayratkochimov-creator/qurilish-erp.git && cd qurilish-erp
python3.13 -m venv venv && venv/bin/pip install -r requirements.txt
```
(Repo yopiq bo'lsa — deploy huquqi beriladi yoki sirlarsiz ZIP olinadi.)

## 2. Sozlamalar (serverning WSGI faylida yoki `.env`)
```
DEBUG=False
SECRET_KEY=<kamida 50 belgili tasodifiy satr>
ALLOWED_HOSTS=<sayt manzili, masalan firma.pythonanywhere.com>
TELEGRAM_2FA_MAJBURIY=1
```
`telegram.json` (loyiha papkasida):
```json
{"token": "<bot tokeni>", "chat_id": "<admin chat_id>"}
```

## 3. Bitta buyruq — bo'sh nusxani tayyorlash
```bash
venv/bin/python manage.py yangi_tashkilot --tekshir
venv/bin/python manage.py yangi_tashkilot --firma "Tashkilot nomi" --admin-parol "<parol>"
```
Buyruq: muhitni tekshiradi → bo'sh bazani yaratadi (bazada ma'lumot bo'lsa to'xtaydi) →
`admin` superuser → firma → `collectstatic` → keyingi qadamlarni chop etadi.

## 4. Ishga tushirish
- PythonAnywhere: Web → Static files `/static/` → `staticfiles`, `/media/` → `media`;
  WSGI faylida env o'zgaruvchilari → **Reload**.
- Telegram webhook: `venv/bin/python manage.py telegram_webhook`
- Admin chat_id ni aniqlash: `venv/bin/python manage.py telegram_chatid`

## 5. Boshlang'ich kiritish (tashkilot bilan birga)
1. `admin` bilan kirish → **Admin panel**.
2. Obyektlar (kod, nom, firma).
3. Foydalanuvchilar → har biriga lavozim: PTO, Snabjeniye, Direktor, Proverchik
   (bir nechta bo'lishi mumkin; bittasi «Asosiy proverchik»), Buxgalter, Prorab; PTO/Prorab/Snab ga
   obyektlar biriktiriladi.
4. Har xodim botga `/start` → admin tasdiqlaydi → xodim login+parol+Telegram kodi bilan kiradi.

## 6. Pilot (1 hafta, bitta obyekt)
PTO umumiy limit → zanjir (snab → PTO xulosasi → direktor → proverchik → admin) →
haftalik so'rov → buxgalter to'lov. Shu jarayonda rol bo'yicha o'rgatish.

## 7. Kundalik ish va yangilanish
- Yangilanish: `cd qurilish-erp && git pull && venv/bin/python manage.py migrate && venv/bin/python manage.py collectstatic --noinput` → Reload.
- Zaxira: kunlik arxiv (baza + hisobotlar ZIP) admin chatiga o'zi keladi; qo'lda:
  `venv/bin/python manage.py telegram_backup`.
- Serverdagi baza — yagona haqiqat manbai; lokal nusxa serverga yuklanmaydi.
