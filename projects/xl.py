"""Excel eksport xavfsizligi (audit M3, 2026-11-12).

openpyxl «=» bilan boshlangan HAR QANDAY matnni formula deb yozadi. Foydalanuvchi qator nomi yoki
izohga «=HYPERLINK(...)» kabi narsa yozsa, u direktor/admin faylni ochganda ishlab ketadi.
Bu yerda fayl saqlanishidan oldin barcha kataklar tekshiriladi: dastur O'ZI yozadigan oddiy
formulalar (katak manzillari, + - * /, SUM/IF/MAX ...) qoladi, qolgani ODDIY MATNGA aylanadi.
"""
import re

_FN_OK = {"SUM", "SUMIF", "IF", "OR", "AND", "N", "MAX", "MIN", "ROUND", "ABS"}
_BELGI = re.compile(r"^=[A-Z0-9+\-*/().,:;<>=$% ]*$")
_FN = re.compile(r"([A-Z]+)\(")
_MATN = re.compile(r'"[^"]*"')     # formuladagi matn literali ("" yoki "Материал") — o'zi xavfsiz


def formula_xavfsiz(v):
    """Dastur yozadigan formulalar ko'rinishimi? Xavf FUNKSIYADA (HYPERLINK, WEBSERVICE, DDE...) —
    shuning uchun faqat ruxsat etilgan funksiyalar va oddiy belgilar qoladi; matn literallari
    (masalan SUMIF sharti) tekshiruvdan oldin olib tashlanadi."""
    s = _MATN.sub("", str(v))
    if not _BELGI.match(s):
        return False
    return all(fn in _FN_OK for fn in _FN.findall(s))


def formulalar(wb):
    """Qolip (template) fayldagi mavjud formulalar — ular ishonchli, tegilmaydi."""
    out = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.data_type == "f":
                    out[(ws.title, c.coordinate)] = c.value
    return out


def xl_tozala(wb, ishonchli=None):
    """Shubhali «formula»larni matnga aylantiradi. Nechta katak o'zgarganini qaytaradi."""
    ishonchli = ishonchli or {}
    n = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.data_type != "f" or not isinstance(c.value, str):
                    continue
                if ishonchli.get((ws.title, c.coordinate)) == c.value:
                    continue
                if not formula_xavfsiz(c.value):
                    c.data_type = "s"      # formula emas — oddiy matn bo'lib yoziladi
                    n += 1
    return n


def xl_saqla(wb, buf, ishonchli=None):
    xl_tozala(wb, ishonchli)
    wb.save(buf)
