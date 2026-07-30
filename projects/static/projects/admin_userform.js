// Admin "Foydalanuvchi qo'shish" formasi uchun jonli tekshiruv:
// login/parol to'g'ri bo'lsa maydon YASHIL, noto'g'ri bo'lsa QIZIL.
// Bu faqat ko'rsatma (hint) — yakuniy tekshiruvni server qiladi.
(function () {
  "use strict";

  function paint(el, state, msg) {
    if (!el) return;
    // state: "ok" | "bad" | "" (neytral)
    el.style.transition = "border-color .15s, box-shadow .15s, background .15s";
    if (state === "ok") {
      el.style.borderColor = "#16a34a";
      el.style.boxShadow = "0 0 0 3px rgba(22,163,74,.15)";
      el.style.background = "#f0fdf4";
    } else if (state === "bad") {
      el.style.borderColor = "#dc2626";
      el.style.boxShadow = "0 0 0 3px rgba(220,38,38,.12)";
      el.style.background = "#fef2f2";
    } else {
      el.style.borderColor = "";
      el.style.boxShadow = "";
      el.style.background = "";
    }
    // Kichik izoh maydoni
    var hintId = el.id + "__hint";
    var hint = document.getElementById(hintId);
    if (!hint) {
      hint = document.createElement("div");
      hint.id = hintId;
      hint.style.fontSize = "12px";
      hint.style.marginTop = "4px";
      hint.style.fontWeight = "600";
      if (el.parentNode) el.parentNode.appendChild(hint);
    }
    hint.textContent = msg || "";
    hint.style.color = state === "ok" ? "#16a34a" : (state === "bad" ? "#dc2626" : "");
  }

  function byName(name) {
    return document.querySelector('input[name="' + name + '"]');
  }

  // Django UnicodeUsernameValidator: harflar (unicode), raqamlar va . @ + - _
  var USERNAME_RE = /^[\p{L}\p{N}.@+\-_]+$/u;

  function checkUsername(u) {
    if (!u) return;
    var v = u.value.trim();
    if (!v) { paint(u, "", ""); return; }
    if (v !== u.value) { paint(u, "bad", "Bo'sh joy (probel) bo'lmasin"); return; }
    if (!USERNAME_RE.test(v)) { paint(u, "bad", "Faqat harf/raqam va . @ + - _ (probelsiz)"); return; }
    paint(u, "ok", "Login to'g'ri ✓");
  }

  function checkPass1(p1, u) {
    if (!p1) return;
    var v = p1.value;
    var uname = u ? u.value.trim().toLowerCase() : "";
    if (!v) { paint(p1, "", ""); return; }
    if (v.length < 8) { paint(p1, "bad", "Kamida 8 belgi (hozir " + v.length + ")"); return; }
    if (/^\d+$/.test(v)) { paint(p1, "bad", "Faqat raqamdan iborat bo'lmasin"); return; }
    if (uname && (v.toLowerCase() === uname || v.toLowerCase().indexOf(uname) !== -1)) {
      paint(p1, "bad", "Parol loginga o'xshamasin"); return;
    }
    paint(p1, "ok", "Parol yaxshi ✓");
  }

  function checkPass2(p1, p2) {
    if (!p2) return;
    var v = p2.value;
    if (!v) { paint(p2, "", ""); return; }
    if (p1 && v === p1.value) { paint(p2, "ok", "Parollar mos ✓"); }
    else { paint(p2, "bad", "Parollar mos emas"); }
  }

  function init() {
    var u = byName("username");
    var p1 = byName("password1");
    var p2 = byName("password2");
    // Faqat qo'shish formasida password1/password2 bo'ladi
    if (u) u.addEventListener("input", function () { checkUsername(u); if (p1) checkPass1(p1, u); });
    if (p1) p1.addEventListener("input", function () { checkPass1(p1, u); if (p2) checkPass2(p1, p2); });
    if (p2) p2.addEventListener("input", function () { checkPass2(p1, p2); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
