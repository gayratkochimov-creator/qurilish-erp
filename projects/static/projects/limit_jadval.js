// Limit jadvali — asosiy skript. Sahifadagi window.LJ_CFG (shablon qiymatlari) dan o'qiydi.
// Statik fayl — brauzer keshlaydi; versiya ?v=<commit> bilan yangilanadi. (2026-10-16 — tezlik)
(function(){
  var C = window.LJ_CFG;
  var TARIX_URL = C.TARIX_URL || '';
  var CAN_EDIT = C.CAN_EDIT;
  var ADMIN_RAW = C.ADMIN_RAW;
  var ADM_TOGRI = C.ADM_TOGRI;
  var IS_ADM = C.IS_ADM;
  var LIM_EDIT = C.LIM_EDIT;
  var TAKLIF = C.TAKLIF;
  var TAKLIF_EDIT = C.TAKLIF_EDIT;
  var TAKLIF_TOKEN = C.TAKLIF_TOKEN;
  var QORALAMA_BOR = C.QORALAMA_BOR;
  var LIM_QOSH = C.LIM_QOSH;
  var TAKLIF_QULF = C.TAKLIF_QULF;
  var QAYTAR_TMPL = C.QAYTAR_TMPL;
  var EXPORT_URL = C.EXPORT_URL;
  var WK_ACT_TMPL = C.WK_ACT_TMPL;
  var CSRF_T = C.CSRF_T;
  var WK_DIR_ACTOR = C.WK_DIR_ACTOR;
  var WK_SNAB_ACTOR = C.WK_SNAB_ACTOR;
  var WK_PROV_ACTOR = C.WK_PROV_ACTOR;
  var DRAFT_ID = C.DRAFT_ID;
  var HAFTA = JSON.parse(document.getElementById('haftaJson').textContent);
  // Умумийni ZANJIRNING HAR BIR ishtirokchisi (PTO/snab/direktor/admin) shu
  // jadvalda tahrirlaydi — admin to'g'ridan, qolganlarda zanjirga so'rov ketadi
  // Zanjirdagi TAKLIF ko'rinishi: bosqich egasi taklif qatorlarini shu jadvalda
  // to'liq tahrirlaydi (avval alohida forma edi) — bloklar yangiBloklar mexanizmida
  // Qulfdagi limitga YANGI blok/qator qo'shish (zanjir orqali) — mavjud qatorlar o'zgarmaydi
  // Qoralama tahririda qulf: asl (mavjud) qatorlar faqat o'qiladi
  var BLOK_EDIT = LIM_EDIT || TAKLIF_EDIT || LIM_QOSH;   // blok/qator qo'shish-o'chirish huquqi
  var taklifDirty = false;                   // saqlanmagan taklif tahriri bor
  var yuborilmoqda = false;                  // forma ketayotganda avtosaqlash yozmasin
  // Haftalik bosqich egasi uchun jadvalning o'zida amal formasi (oldinga / orqaga)
  function wkForma(id, action, label, rang, sababKerak){
    var f = '<form method="post" action="'+WK_ACT_TMPL.replace('123456789', id)+'" class="'+(sababKerak?'wk-sabab-f':'')+'" style="margin:0; display:inline-flex; gap:5px; align-items:center;">'+
      '<input type="hidden" name="csrfmiddlewaretoken" value="'+CSRF_T+'"><input type="hidden" name="next" value="jadval"><input type="hidden" name="action" value="'+action+'">'+
      (sababKerak ? '<input type="text" name="reject_note" placeholder="сабаб..." style="padding:3px 6px; border:1px solid #c8ccd0; border-radius:4px; font-size:12px; width:140px;">' : '')+
      '<button type="submit" style="border:none; border-radius:5px; background:'+rang+'; color:#fff; padding:4px 13px; font-weight:700; font-size:12px; cursor:pointer;">'+label+'</button></form>';
    return f;
  }
  // Sabab majburiy (orqaga qaytarish formalari)
  document.addEventListener('submit', function(e){
    var f = e.target;
    if (f.classList && f.classList.contains('wk-sabab-f')){
      var s2 = f.querySelector('[name=reject_note]');
      if (s2 && !s2.value.trim()){ e.preventDefault(); alert('Қайтариш сабабини ёзинг.'); s2.focus(); }
    }
  });
  var yangiBloklar = [];   // admin qo'shgan yangi limit bloklari (saqlanmagan)
  if (TAKLIF_EDIT){
    try { yangiBloklar = JSON.parse(document.getElementById('taklifJson').textContent) || []; } catch(e){}
  }
  var ybSubs = [];         // yangi/taklif bloklarining «Блок якуни» qatorlari
  // Mavjud limit qatorlari/bloklarining TAHRIR holati (LIM_EDIT): id -> {...}, bolim0 -> {bolim, masul}
  var limRows = {}, limBloklar = {};
  function limRow(tr){
    var id = tr.dataset.id;
    return limRows[id] || (limRows[id] = { name:tr.dataset.name, unit:tr.dataset.unit, kind:tr.dataset.kind||'material',
      izoh:tr.dataset.izoh||'', vol:pf(tr.dataset.vol), price:pf(tr.dataset.price),
      bq:pf(tr.dataset.bq), bs:pf(tr.dataset.bs), del:false });
  }
  function limBlok(bolim0){
    var b = document.querySelector('#ljBody tr.block[data-bolim="'+CSS.escape(bolim0)+'"]');
    return limBloklar[bolim0] || (limBloklar[bolim0] = { bolim:bolim0, masul:(b ? b.dataset.masul : '')||'' });
  }
  function blkBolim(blk){ return blk.ichida !== undefined ? limBlok(blk.ichida).bolim : (blk.bolim||''); }
  function blkMasul(blk){ return blk.ichida !== undefined ? limBlok(blk.ichida).masul : (blk.masul||''); }
  var KSEL = '<select class="ksel lim-k"><option value="material">Материал</option><option value="labor">Иш ҳақи</option><option value="machinery">Машина части</option><option value="other">Кўзда тутилмаган</option><option value="avans">Аванс</option></select>';
  var STATUS_NOMI = { dir:"Директор тасдиғида", submitted:"Админ тасдиғида", approved:"Тасдиқланган", draft:"Қоралама" };
  var sel = DRAFT_ID !== null ? ('w'+DRAFT_ID)
            : (CAN_EDIT ? 'yangi' : (HAFTA.length ? 'w'+HAFTA[HAFTA.length-1].id : 'yangi'));
  // ?hafta=<id> — ruxsat bilan tahrirlangan hafta saqlangach shu haftada qolamiz
  (function(){
    var m = /[?&]hafta=(\d+)/.exec(location.search);
    if (!m) return;
    for (var i = 0; i < HAFTA.length; i++) if (String(HAFTA[i].id) === m[1]) { sel = 'w' + m[1]; break; }
    try { history.replaceState(null, '', location.pathname + location.search.replace(/([?&])hafta=\d+&?/, '$1').replace(/[?&]$/, '') + location.hash); } catch (e) {}
  })();
  var bolaklar = {};    // key -> [{qty, price, izoh}]
  var extralar = [];    // limitda yo'q ishlar: {name, bolim, unit, kind, qty, price, izoh}

  function fmt(n){ n = Math.round(n*1000)/1000; return n.toLocaleString('ru-RU', {maximumFractionDigits: 3}); }   // 3 kasr: 253,185 — yashirin 0,005 lar ko'rinsin (2026-10-21)
  function pf(v){ var x = parseFloat(String(v).replace(/\s/g,'').replace(',','.')); return isNaN(x)?0:x; }
  function esc(s){ var d=document.createElement('div'); d.textContent=s==null?'':String(s); return d.innerHTML.replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
  // Ҳафталик изоҳ: ✎ тугмаси билан очилади, ёзиб бўлгач (фокус кетганда) ўзи ёпилади;
  // изоҳ бор бўлса тугма сариқ ва матни кўрсатилади
  function wiBelgi(btn, ta){
    if (!btn || !ta) return;
    var bor = !!(ta.value||'').trim();
    btn.classList.toggle('has', bor);
    btn.title = bor ? ('Изоҳ: '+ta.value.trim()+' (очиш/ёпиш)') : 'Ҳафталикка изоҳ ёзиш';
  }
  function wiUla(btn, ta){
    if (!btn || !ta) return;
    ta.style.display = 'none';
    wiBelgi(btn, ta);
    btn.onclick = function(){
      var ochiq = ta.style.display === 'none';
      ta.style.display = ochiq ? '' : 'none';
      if (ochiq){ ta.focus(); izohKengay(ta); }
    };
    ta.addEventListener('blur', function(){ ta.style.display = 'none'; wiBelgi(btn, ta); });
    ta.addEventListener('input', function(){ wiBelgi(btn, ta); });
  }
  // (2026-10-23) Kengaytirish BATCH: so'rovlar navbatga yig'ilib keyingi kadrda bir o'tishda bajariladi —
  // har chaqiruvda alohida reflow (80 qatorda ~1 s) o'rniga ~10 ms. O'lchash: avval hammasi 'auto', keyin o'qish, keyin yozish.
  var kengayNavbat = [], kengayKadr = null, kengayTimer = null;
  function izohKengay(el){
    if (!el || el.tagName!=='TEXTAREA') return;
    if (kengayNavbat.indexOf(el) < 0) kengayNavbat.push(el);
    if (!kengayKadr){
      kengayKadr = requestAnimationFrame(kengayIshla);
      kengayTimer = setTimeout(kengayIshla, 150);   // yashirin oynada rAF kelmaydi — zaxira
    }
  }
  function kengayIshla(){
    if (kengayKadr) cancelAnimationFrame(kengayKadr);
    clearTimeout(kengayTimer); kengayKadr = null; kengayTimer = null;
    var ts = kengayNavbat.filter(function(t){ return t.isConnected && t.offsetParent !== null; }); kengayNavbat = [];
    var eski = ts.map(function(t){ return t.style.height; });
    ts.forEach(function(t){ t.style.height = 'auto'; });
    var hs = ts.map(function(t){ return t.scrollHeight; });
    ts.forEach(function(t, i){ t.style.height = hs[i] ? (hs[i] + 2) + 'px' : eski[i]; });
  }
  // Saqlash/yuklashdan keyin, qator ko'rinish rejimi yoki qidiruv o'zgarganda — barcha nom/izoh kataklari qayta o'lchanadi
  // (yashirin paytda o'lchangan katak 1 qatorli bo'lib qoladi)
  // (2026-10-23) BATCH: avval hammasini 'auto' qilib, keyin o'lchab, keyin yozamiz — 246 ta katak uchun
  // 1 000 ms o'rniga ~10 ms (har katakda alohida o'lchash butun jadvalni qayta joylashtirardi)
  function nomKengayHammasi(){
    document.querySelectorAll('#ljBody textarea, #yangiBody textarea, #boshqaBody textarea').forEach(function(t){ if (kengayNavbat.indexOf(t) < 0) kengayNavbat.push(t); });
    kengayIshla();
  }
  window.addEventListener('load', nomKengayHammasi);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(nomKengayHammasi);
  // Katta summa yozilsa raqam katagi YONGA kengayadi (qiymat ko'rinib turadi)
  function raqamKengay(el){
    if (!el || el.type !== 'number') return;
    if (el.dataset.w0 === undefined){
      el.dataset.w0 = el.style.width || '';
      el.dataset.w0px = el.offsetWidth || 88;
    }
    var n = String(el.value || '').length;
    var kerak = (n + 3) * 8;   // ~1 raqam ≈ 8px (zaxira bilan)
    if (kerak > (+el.dataset.w0px)) el.style.width = Math.min(kerak, 170) + 'px';
    else el.style.width = el.dataset.w0;
  }
  function hammaRaqamKengay(){
    var t = document.getElementById('ljTbl');
    if (t) t.querySelectorAll('input[type=number].wkin').forEach(raqamKengay);
  }
  // Kalit: nom + bo'lim + TUR (serverdagi _lj_key bilan bir xil)
  function wkKey(n, b, k){ return (n||'').trim().toLowerCase()+'|'+(b||'').trim().toLowerCase()+'|'+(k||'').trim().toLowerCase(); }
  // Tanlangan haftaning shu nom+bo'lim+tur bo'yicha qiymatlari (limitda yo'q qatorlar ham)
  function wkItems(h, name, bolim, kind){
    if (!h) return null;
    var k = wkKey(name, bolim, kind);
    var arr = h.items[k] || (h.boshqa||[]).filter(function(b){ return wkKey(b.name, b.bolim, b.kind) === k; });
    if (!arr || !arr.length) return null;
    var q = 0; arr.forEach(function(a){ q += (a.qty||0); });
    return { qty:q, price:arr[0].price||0, izoh:arr[0].izoh||'' };
  }
  function sanaQisqa(v, yil){
    if (!v) return '';
    var b = String(v).split('-');
    if (b.length !== 3) return v;
    return b[2] + '.' + b[1] + (yil ? '.' + b[0] : '');
  }
  function wkHeadYangila(){
    var th = document.getElementById('wkHead'); if (!th) return;
    var h = haftaTop(sel), edit = tahrirmi();
    var raqam = h ? h.raqam : (HAFTA.length + 1);
    var wsI = document.getElementById('wkStart'), weI = document.getElementById('wkEnd');
    var ws = (edit && wsI && wsI.value) ? wsI.value : (h ? h.ws : ''), we = (edit && weI && weI.value) ? weI.value : (h ? h.we : '');
    var davr = (ws && we) ? (sanaQisqa(ws) + ' – ' + sanaQisqa(we, true)) : (ws ? sanaQisqa(ws, true) : 'сана киритилмаган');
    th.textContent = raqam + '-ҳафта · ' + davr + ' ' + (edit ? '✏️' : '🔒');
    var pill = document.getElementById('wkPill');
    if (pill && (!h || edit)) pill.textContent = raqam + '-ҳафта · ' + davr;
  }
  function haftaTop(id){ for(var i=0;i<HAFTA.length;i++) if('w'+HAFTA[i].id===id) return HAFTA[i]; return null; }
  function tahrirmi(){ var h=haftaTop(sel); return CAN_EDIT && (sel==='yangi' || (h && !h.locked)); }
  function ishRows(){ return Array.prototype.slice.call(document.querySelectorAll('#ljBody tr.ish')); }

  // -------- ТАБЛАР --------
  // Ekranda faqat OXIRGI 6 hafta tabi (foydalanuvchi qarori, 2026-10-21); eskilari
  // «Ҳафталик тарих»da. Tanlangan (havola bilan ochilgan) eski hafta tabi baribir ko'rsatiladi.
  var TAB_SONI = 6, eskiOchiq = false;
  function renderTabs(){
    var t = document.getElementById('ljTabs'); t.innerHTML='';
    var yashirin = [];
    var korsat = HAFTA.filter(function(h, i){
      var ohirgi = i >= HAFTA.length - TAB_SONI;
      if (ohirgi || eskiOchiq || 'w'+h.id === sel) return true;
      yashirin.push(h); return false;
    });
    if (yashirin.length){
      var e = document.createElement('a');
      e.href = TARIX_URL || '#'; e.className = 'eski';
      e.textContent = '… эски ' + yashirin.length + ' ҳафта';
      e.title = 'Эски ҳафталар «Ҳафталик тарих»да. Шу ерда очиш учун босинг (ўнг тугма — тарихга ўтиш)';
      e.style.cssText = 'font-size:12px; color:#6B7280; text-decoration:none; padding:0 8px; align-self:center; white-space:nowrap;';
      e.onclick = function(ev){ ev.preventDefault(); eskiOchiq = true; renderTabs(); };
      t.appendChild(e);
    } else if (eskiOchiq && HAFTA.length > TAB_SONI){
      var y = document.createElement('a');
      y.href = '#'; y.textContent = '‹ фақат охирги ' + TAB_SONI + ' та';
      y.style.cssText = 'font-size:12px; color:#6B7280; text-decoration:none; padding:0 8px; align-self:center; white-space:nowrap;';
      y.onclick = function(ev){ ev.preventDefault(); eskiOchiq = false; renderTabs(); };
      t.appendChild(y);
    }
    korsat.forEach(function(h){
      var b = document.createElement('button');
      var belgi = h.ruxsat ? '🔓 таҳрир' : h.status==='draft' ? '✏️' : h.status==='dir' ? '⏳ дир.' :
                  h.status==='submitted' ? '⏳ адм.' : '🔒';
      b.textContent = h.raqam+'-ҳафта ('+h.sana+') ' + belgi;
      b.title = STATUS_NOMI[h.status] || h.status;
      b.className = (h.locked?'closed ':'') + ('w'+h.id===sel?'active':'');
      b.onclick = function(){ sel='w'+h.id; renderAll(); chizExtralar(); autoTikla(); };
      t.appendChild(b);
    });
    if (CAN_EDIT && DRAFT_ID===null){
      var b = document.createElement('button');
      b.textContent = '+ Янги ҳафта';
      b.className = 'yangi ' + (sel==='yangi'?'active':'');
      b.onclick = function(){ sel='yangi'; renderAll(); chizExtralar(); autoTikla(); };
      t.appendChild(b);
    }
  }

  // -------- ЖАДВАЛ (таб алмашганда тоза қурилади) --------
  var korilganKey = {};   // renderAll ичида: ном+бўлим калити биринчи марта кўрилдими
  function renderAll(){
    renderTabs();
    korilganKey = {};
    var h = haftaTop(sel);
    var edit = tahrirmi();
    var raqam = h ? h.raqam : (HAFTA.length+1);
    if (edit && h){
      var ws0 = document.getElementById('wkStart'), we0 = document.getElementById('wkEnd');
      if (ws0) ws0.value = h.ws;
      if (we0) we0.value = h.we;
    }
    wkHeadYangila();
    // ---- ҲАФТАЛИК КАРТА: ҳолат плитаси, 6 босқичли занжир, амаллар (ҳафта табига қараб) ----
    (function(){
      var pill = document.getElementById('wkPill'), tile = document.getElementById('wkTile'),
          stepsEl = document.getElementById('wkSteps'), amal = document.getElementById('wkAmal'),
          prev = document.getElementById('wkPrev'), note = document.getElementById('wkNote');
      if (!pill || !tile || !stepsEl) return;
      var TARTIB = ['draft','snab','pto2','dir','prov','submitted'];
      var NOM = {draft:'ПТО', snab:'Снабжения', pto2:'ПТО хулосаси', dir:'Директор', prov:'Проверчик', submitted:'Админ'};
      var KUT = {snab:'Снабжения нархлашида', pto2:'ПТО хулосасида', dir:'Директор тасдиғида', prov:'Проверчик текширувида', submitted:'Админ тасдиғида'};
      function step(i, nom, holat, meta){
        return '<div class="lh-step '+holat+'"><span class="n">'+(holat==='ok' ? '✓' : i)+'</span><span class="nm">'+esc(nom)+'</span>'+
               (meta ? '<span class="mt" title="'+esc(meta)+'">'+esc(meta)+'</span>' : '')+'</div>';
      }
      function zanjir(status, meta){
        // status: draft → 1-қадам жорий; approved → ҳаммаси ✓
        var ji = status === 'approved' ? 99 : TARTIB.indexOf(status);
        if (ji < 0) ji = 0;
        return TARTIB.map(function(st, i){
          var holat = i < ji ? 'ok' : (i === ji ? 'joriy' : 'keyin');
          return step(i + 1, NOM[st], holat, holat === 'joriy' ? (status === 'draft' ? (meta.draft || 'ҳозир') : 'кутилмоқда') : (meta[st] || ''));
        }).join('');
      }
      function tileHtml(cls, tl, b, mt){
        tile.className = 'lh-tile ' + cls;
        tile.innerHTML = '<span class="tl">'+esc(tl)+'</span><b>'+b+'</b>'+(mt ? '<span class="mt">'+mt+'</span>' : '');
      }
      function orqagaForma(id, targetlar){
        var opts = targetlar.map(function(t){ return '<option value="'+t+'">'+(t === 'draft' ? 'ПТО (қоралама)' : NOM[t])+'га</option>'; }).join('');
        return '<form method="post" action="'+WK_ACT_TMPL.replace('123456789', id)+'" class="wk-sabab-f" style="margin:0; display:inline-flex; gap:5px; align-items:center;">'+
          '<input type="hidden" name="csrfmiddlewaretoken" value="'+CSRF_T+'"><input type="hidden" name="next" value="jadval"><input type="hidden" name="action" value="orqaga">'+
          '<select name="target" style="height:34px; padding:0 6px; border:1px solid #D5D9E0; border-radius:6px; font-size:12px;">'+opts+'</select>'+
          '<input type="text" name="reject_note" placeholder="сабаб..." style="width:140px;">'+
          '<button type="submit" style="border:none; border-radius:8px; background:#B45309; color:#fff; padding:0 14px; height:34px; font-weight:700; font-size:12.5px; cursor:pointer;">↩ Орқага қайтариш</button></form>';
      }
      var tasdiq = HAFTA.filter(function(x){ return x.status === 'approved'; }).length;
      var boshqa = HAFTA.length - tasdiq;
      prev.textContent = HAFTA.length
        ? (tasdiq + ' та тасдиқланган, қулфланган' + (boshqa ? ' · ' + boshqa + ' та жараёнда' : ''))
        : 'ҳали ҳафта йўқ';
      var ruxsatTugma = (h && h.status !== 'draft' && ADMIN_RAW)
        ? wkForma(h.id, 'tahrir_ruxsat', h.ruxsat ? '🔒 Рухсатни ёпиш' : '🔓 Таҳрирга рухсат бериш', h.ruxsat ? '#b45309' : '#1E5BB8', false) : '';
      var ruxsatMt = (h && h.ruxsat) ? ' · 🔓 админ таҳрирга рухсат берган (бир марталик) — ўзгартириб «Сақлаш»ни босинг' : '';
      var acts = '';
      if (!h){
        pill.textContent = raqam + '-ҳафта · янги';
        tileHtml('warn', 'Ҳафта ҳолати', 'Янги ҳафта, сақланмаган', 'Ўзгаришлар фақат шу браузерда — «Сақлаш» қоралама қилади');
        stepsEl.innerHTML = zanjir('draft', {draft:'сиз'});
      } else {
        pill.textContent = h.raqam + '-ҳафта · ' + h.sana;
        var meta = {draft: h.kiritdi||'', snab: h.snab_by||'', pto2: h.pto2_by||'', dir: h.dir_by||'', prov: h.prov_by||'', submitted: h.adm_by||''};
        var kir = (h.kiritdi||'') + (h.kiritdi_at ? ' · ' + h.kiritdi_at : '');
        var qayt = (h.qaytarish ? '<br><span style="color:#B91C1C;">'+esc(h.qaytarish)+'</span>' : '');
        if (h.status === 'draft'){
          tileHtml('warn', 'Ҳафта ҳолати', 'Қоралама — ҳали юборилмаган', esc(kir) + esc(ruxsatMt) + qayt);
          stepsEl.innerHTML = zanjir('draft', meta);
        } else if (h.status === 'approved'){
          tileHtml('ok2', 'Ҳафта ҳолати', '✓ Тасдиқланган', esc((h.adm_by||'admin') + (h.adm_at ? ' · ' + h.adm_at : '')) + esc(ruxsatMt));
          stepsEl.innerHTML = zanjir('approved', meta);
          if (ADMIN_RAW){
            acts += ruxsatTugma +
              '<a href="'+QAYTAR_TMPL.replace('123456789', h.id)+'" class="lh-btn xs" style="color:#92400E; border-color:#F3D9A4;" '+
              'title="Сўров ПТО қораламасига қайтади — таҳрирлаб қайта юборади. Телеграм код сўралади.">↩ ПТОга қайтариш</a>';
          }
        } else {
          tileHtml('blue', 'Ҳафта ҳолати', KUT[h.status] || h.status, 'ПТО юборди: ' + esc(kir) + esc(ruxsatMt) + qayt);
          stepsEl.innerHTML = zanjir(h.status, meta);
          // Жорий босқич эгаси — олдинга / орқага
          var ji = TARTIB.indexOf(h.status), orq = TARTIB.slice(0, ji);
          if (h.status === 'snab' && (WK_SNAB_ACTOR || ADMIN_RAW)) acts += wkForma(h.id, 'snab_return', '✓ Нархланди — ПТО хулосасига', '#15803D', false);
          else if (h.status === 'pto2' && CAN_EDIT) acts += wkForma(h.id, 'pto_send_dir', '▶ Директорга юбориш', '#15803D', false);
          else if (h.status === 'dir' && WK_DIR_ACTOR) acts += wkForma(h.id, 'dir_approve', '✓ Тасдиқлаш (директор)', '#15803D', false);
          else if (h.status === 'prov' && (WK_PROV_ACTOR || ADMIN_RAW)) acts += wkForma(h.id, 'prov_approve', '✓ Текширилди — админга', '#15803D', false);
          else if (h.status === 'submitted' && ADMIN_RAW) acts += wkForma(h.id, 'approve', '✓ Тасдиқлаш (админ)', '#15803D', false);
          var egasi = (h.status === 'snab' && WK_SNAB_ACTOR) || (h.status === 'pto2' && CAN_EDIT) || (h.status === 'dir' && WK_DIR_ACTOR) ||
                      (h.status === 'prov' && WK_PROV_ACTOR) || ADMIN_RAW;
          if (egasi){
            var targ = ADMIN_RAW ? TARTIB.filter(function(t){ return t !== h.status && t !== 'submitted'; }) : orq;
            if (h.status === 'pto2' && !ADMIN_RAW) targ = ['draft', 'snab'];
            if (h.status === 'prov' && !ADMIN_RAW) targ = ['draft', 'dir'];
            if (targ.length) acts += orqagaForma(h.id, targ);
          }
          acts += ruxsatTugma;
        }
      }
      amal.innerHTML = acts;
      if (note){
        var ns = note.querySelector('span');
        if (ns) ns.textContent = (h && h.ruxsat) ? 'Рухсат бир марталик: сақлангач ҳафта яна қулфланади (ҳолати ўзгармайди).'
                                : (h && h.status !== 'draft') ? 'Ҳафта қулф — қайта таҳрир учун админ рухсати керак.'
                                : 'Ҳафта юборилгач унинг устунлари қулфланади ва занжирга (снабжения → ПТО хулосаси → директор → проверчик → админ) ўтади.';
      }
    })();
    document.querySelectorAll('#ljBody tr.split').forEach(function(x){ x.remove(); });

    if (LIM_EDIT){
      // Blok sarlavhasi: bo'lim nomi va mas'ul ham tahrirlanadi
      document.querySelectorAll('#ljBody tr.block:not(.exhdr)').forEach(function(bt){
        var b = limBlok(bt.dataset.bolim);
        bt.cells[1].innerHTML = '<span style="display:inline-flex; gap:8px; align-items:center; flex-wrap:wrap;">'+
          '<input class="wkin tx lim-bn" value="'+esc(b.bolim)+'" placeholder="бўлим номи" style="width:280px; font-weight:700;">'+
          '<input class="wkin tx lim-bm" value="'+esc(b.masul)+'" placeholder="масъул (ихтиёрий)" style="width:190px;"></span>';
        bt.querySelector('.lim-bn').addEventListener('input', function(){ b.bolim = this.value; autoSaqla(); });
        bt.querySelector('.lim-bm').addEventListener('input', function(){ b.masul = this.value; autoSaqla(); });
      });
    }
    ishRows().forEach(function(tr){
      var key = tr.dataset.key;
      var vol = pf(tr.dataset.vol), price = pf(tr.dataset.price);
      var fq = pf(tr.dataset.fqty), fs = pf(tr.dataset.fsum);
      if (LIM_EDIT){
        // Умумий қатори ТЎЛИҚ таҳрирланади — янги блок қаторлари билан бир хил кўриниш
        var x = limRow(tr);
        vol = x.vol; price = x.price;
        tr.style.display = x.del ? 'none' : '';
        tr.cells[1].innerHTML = '<textarea class="wkin tx lim-n" rows="1" placeholder="иш номи..." title="'+(tr.dataset.sana ? 'Киритилган: '+esc(tr.dataset.sana) : '')+'">'+esc(x.name)+'</textarea>'+KSEL;
        izohKengay(tr.cells[1].querySelector('.lim-n'));
        var ksel = tr.querySelector('.lim-k'); ksel.value = x.kind || 'material';
        turChipUla(tr, ksel, x);
        tr.cells[3].innerHTML = '<input class="wkin kichik lim-u" value="'+esc(x.unit)+'" placeholder="бирл.">';
        tr.querySelector('.c-vol').innerHTML = '<input class="wkin lim-q" type="number" min="0" step="0.001" value="'+(vol||'')+'">';
        tr.querySelector('.c-price').innerHTML = '<input class="wkin lim-p" type="number" min="0" value="'+(price||'')+'">';
        // Бажарилган: лимитгача олинган қисм ҚЎЛДА (ҳафталиклар устига автоматик қўшилади)
        var wq0 = pf(tr.dataset.wq), ws0 = pf(tr.dataset.ws);
        // Katakda ЖАМИ бажарилган: ёзилган ҳафталиклар (автоматик) + қўлда киритилган қисм.
        // Фойдаланувчи ёзган сон жами ҳисобланади — ҳафталиклар ундан айирилиб, қолгани қўлда қисм бўлади
        var hint = ' title="Жами бажарилган = ҳафталиклар ('+fmt(wq0)+' / '+fmt(ws0)+', автоматик) + қўлда киритилган қисм"';
        tr.querySelector('.c-fqty').innerHTML = '<input class="wkin lim-bq" type="number" min="0" step="0.001" value="'+(((x.bq||0)+wq0)||'')+'" placeholder="0"'+hint+'>';
        tr.querySelector('.c-fsum').innerHTML = '<input class="wkin lim-bs" type="number" min="0" value="'+(((x.bs||0)+ws0)||'')+'" placeholder="0"'+hint+'>';
        fq = wq0 + (x.bq||0); fs = ws0 + (x.bs||0);
      } else {
        tr.querySelector('.c-vol').textContent = fmt(vol);
        tr.querySelector('.c-price').textContent = fmt(price);
        tr.querySelector('.c-fqty').textContent = fmt(fq);
        tr.querySelector('.c-fsum').textContent = fmt(fs);
      }
      tr.querySelector('.c-total').textContent = fmt(vol*price);
      tr.querySelector('.c-lqty').textContent = fmt(vol-fq);
      tr.querySelector('.c-lsum').textContent = fmt(vol*price-fs);

      var items = (h && h.items[key]) ? h.items[key] : [];
      var wq = tr.querySelector('.c-wq'), wp = tr.querySelector('.c-wp'),
          ws = tr.querySelector('.c-ws'), am = tr.querySelector('.c-amal'),
          iz = tr.querySelector('.c-izoh');
      // Бир хил ном + бўлим ИККИНЧИ марта: ҳафталик фақат биринчи қаторга тегишли
      var dubl = !!korilganKey[key]; korilganKey[key] = true;
      if (dubl){
        tr.dataset.dubl = '1';
        wq.innerHTML = '<span class="mute" style="font-size:11px; color:#B45309; white-space:normal;" title="Бу блокда шу номли қатор юқорида бор — ҳафталик ўша қаторга ёзилади. Бошқа иш бўлса, номини фарқланг (масалан изоҳини номга қўшинг).">↑ бир хил ном</span>';
        wp.textContent = ''; ws.textContent = ''; am.textContent = '';
        if (LIM_EDIT){ iz.innerHTML = '<textarea class="wkin tx lim-i" rows="1" placeholder="изоҳ...">'+esc(limRow(tr).izoh)+'</textarea>'; izohKengay(iz.querySelector('.lim-i')); }
        else iz.textContent = tr.dataset.izoh || '';
      } else {
        delete tr.dataset.dubl;
      }
      if (dubl){
        // (ҳафталик катаги йўқ — пастдаги шохлар ўтказиб юборилади)
      } else if (edit){
        var m = items.length ? items[0] : null;
        var q0 = m ? m.qty : 0, p0 = m ? m.price : price;
        // Ҳафталик изоҳ — ✎ тугмаси орқасида (ёзиб бўлгач ўзи ёпилади); Изоҳ устуни — ЛИМИТ изоҳи
        wq.innerHTML = '<span style="display:flex; gap:3px; align-items:flex-start;">'+
          '<input class="wkin in-q" type="number" min="0" step="0.001" value="'+(q0||'')+'" placeholder="0" style="flex:1; min-width:56px;">'+
          '<button type="button" class="wi-tgl in-i-tgl">✎</button>'+
          '<textarea class="wkin tx in-i" rows="1" placeholder="ҳафталик изоҳ..." style="display:none; flex:1; min-width:110px;">'+esc(m?m.izoh:'')+'</textarea></span>';
        wiUla(wq.querySelector('.in-i-tgl'), wq.querySelector('.in-i'));
        if (LIM_EDIT){
          iz.innerHTML = '<textarea class="wkin tx lim-i" rows="1" placeholder="изоҳ...">'+esc(limRow(tr).izoh)+'</textarea>';
          izohKengay(iz.querySelector('.lim-i'));
        } else {
          iz.textContent = tr.dataset.izoh || '';
        }
        wp.innerHTML = '<input class="wkin in-p" type="number" min="0" value="'+(p0||'')+'">';
        ws.textContent = fmt(q0*p0);
        am.innerHTML = '<button type="button" class="split-btn" title="Нарх ўзгарди — қаторни бўлиш">х</button>';
        am.querySelector('.split-btn').onclick = function(){ ochModal(key); };
        bolaklar[key] = items.slice(1).map(function(x){ return { qty:x.qty, price:x.price, izoh:x.izoh }; });
        chizBolaklar(tr, key, true);
      } else if (LIM_EDIT){
        // Ҳафта ёпиқ, лекин Умумий таҳрирланади (снаб/директор ёки ёпиқ ҳафта табида)
        wq.textContent = items.length ? fmt(items[0].qty) : '';
        wp.textContent = items.length ? fmt(items[0].price) : '';
        ws.textContent = items.length ? fmt(items[0].sum) : '';
        am.textContent = '';
        iz.innerHTML = '<textarea class="wkin tx lim-i" rows="1" placeholder="изоҳ...">'+esc(limRow(tr).izoh)+'</textarea>';
        izohKengay(iz.querySelector('.lim-i'));
        bolaklar[key] = items.length>1 ? items.slice(1).map(function(x){ return { qty:x.qty, price:x.price, izoh:x.izoh }; }) : [];
        if (items.length>1) chizBolaklar(tr, key, false);
      } else {
        wq.textContent = items.length ? fmt(items[0].qty) : '';
        wp.textContent = items.length ? fmt(items[0].price) : '';
        ws.textContent = items.length ? fmt(items[0].sum) : '';
        am.textContent = '';
        iz.textContent = items.length && items[0].izoh ? items[0].izoh : (tr.dataset.izoh||'');
        if (items.length>1){
          bolaklar[key] = items.slice(1).map(function(x){ return { qty:x.qty, price:x.price, izoh:x.izoh }; });
          chizBolaklar(tr, key, false);
        } else bolaklar[key] = [];
      }
      if (LIM_EDIT){
        // ✕ — мавжуд қаторни ўчириш (сақлашда занжирга/тўғридан кетади)
        var db = document.createElement('button'); db.type = 'button'; db.className = 'del-btn lim-del'; db.title = 'Қаторни ўчириш'; db.textContent = '✕';
        db.onclick = function(){
          var x0 = limRow(tr);
          if (fq > 0 && !confirm('«'+x0.name+'» бўйича бажарилган иш бор ('+fmt(fq)+'). Қатор барибир ўчирилсинми?')) return;
          if (fq <= 0 && !confirm('«'+x0.name+'» қатори лимитдан ўчирилсинми?')) return;
          x0.del = true; tr.style.display = 'none';
          document.querySelectorAll('#ljBody tr.split[data-key="'+CSS.escape(key)+'"]').forEach(function(t){ t.style.display = 'none'; });
          hisobla();
        };
        am.appendChild(db);
      }
    });
    // limitda yo'q qatorlar
    extralar = (h && h.boshqa) ? h.boshqa.map(function(x){
      return { name:x.name, bolim:x.bolim, unit:x.unit, kind:x.kind||'material',
               qty:x.qty, price:x.price, izoh:x.izoh||'' };
    }) : [];
    chizExtralar();
    chizYangiBloklar();
    hisobla();
  }

  function rowDel(tr){ var x = LIM_EDIT && tr.dataset.id ? limRows[tr.dataset.id] : null; return !!(x && x.del); }
  function rowDelKey(key){ if (!LIM_EDIT || !key) return false; var tr = document.querySelector('#ljBody tr.ish[data-key="'+CSS.escape(key)+'"]'); return !!(tr && rowDel(tr)); }
  function rowVol(tr){ var i=tr.querySelector('.lim-q'); return i?pf(i.value):pf(tr.dataset.vol); }
  function rowPrice(tr){ var i=tr.querySelector('.lim-p'); return i?pf(i.value):pf(tr.dataset.price); }

  // -------- + Блок (бўлим) — админ янги лимит блоки қўшади --------
  function chizYangiBloklar(){
    var tb = document.getElementById('yangiBody'); tb.innerHTML='';
    ybSubs = [];
    document.querySelectorAll('#ljBody tr.extra.yb-ich, #ljBody tr.yb-keyin').forEach(function(t){ t.remove(); });
    yangiBloklar = yangiBloklar.filter(function(b){ return b.ichida === undefined || b.rows.length; });
    if (!yangiBloklar.some(function(b){ return b.ichida === undefined && b.keyin === undefined; })){
      // Blok yo'q bo'lsa ham jadval ICHIDA qo'shish tugmasi turadi
      if (BLOK_EDIT){
        var addr0 = document.createElement('tr'); addr0.className = 'yb-addrow';
        addr0.innerHTML = '<td style="padding:0;"></td><td colspan="16" style="background:#f6f9fd; padding:1px 8px;">'+
          '<button type="button" class="blk-add yb-addblok0" style="margin-left:0; padding:0 8px; font-size:11px; line-height:17px;">+ Янги блок (бўлим)</button></td>';
        addr0.querySelector('.yb-addblok0').onclick = yangiBlokQosh;
        tb.appendChild(addr0);
      }
      if (!yangiBloklar.length) return;
    }
    yangiBloklar.forEach(function(blk, bi){
      // Mavjud blok ICHIGA qo'shilgan qatorlar — sarlavhasiz, o'sha blokning «+ Қатор» qatori oldiga
      var ichAnchor = null;
      if (blk.ichida !== undefined){
        ichAnchor = document.querySelector('#ljBody tr.lim-addrow[data-bolim="'+CSS.escape(blk.ichida)+'"]');
        if (!ichAnchor || !(LIM_EDIT || LIM_QOSH)) return;
      }
      // Mavjud blokdan KEYIN qo'shilgan yangi blok — o'sha blok yakunidan so'ng chiziladi
      var keyinAnchor = null;
      if (blk.keyin !== undefined && (LIM_EDIT || LIM_QOSH)){
        var la = document.querySelector('#ljBody tr.lim-addrow[data-bolim="'+CSS.escape(blk.keyin)+'"]');
        keyinAnchor = la ? la.nextElementSibling : null;   // «Блок якуни» qatori
        while (keyinAnchor && keyinAnchor.nextElementSibling && keyinAnchor.nextElementSibling.classList.contains('yb-keyin'))
          keyinAnchor = keyinAnchor.nextElementSibling;
      }
      function ins(el){
        if (keyinAnchor){ el.classList.add('yb-keyin'); keyinAnchor.after(el); keyinAnchor = el; }
        else tb.appendChild(el);
      }
      var hr = document.createElement('tr'); hr.className='block'; hr.dataset.bi = bi;
      hr.innerHTML = '<td></td><td colspan="14"><span style="display:inline-flex; gap:8px; align-items:center; flex-wrap:wrap;">'+
        '<input class="wkin tx yb-bolim" value="'+esc(blk.bolim||'')+'" placeholder="'+(TAKLIF_EDIT?'бўлим номи (бўш — «Бўлимсиз қаторлар»)':'бўлим номи (масалан: 1-этаж иш хаклари)')+'" style="width:280px; font-weight:700;">'+
        '<input class="wkin tx yb-masul" value="'+esc(blk.masul||'')+'" placeholder="масъул (ихтиёрий)" style="width:190px;">'+
        (TAKLIF_EDIT ? '' : '<span style="font-weight:400; font-size:11px; color:#8a5a00;">(янги блок — «Лимитни юбориш» билан занжирга кетади)</span>')+'</span></td>'+
        '<td class="ctr"><button type="button" class="del-btn" title="Блокни ўчириш">✕</button></td>';
      if (TAKLIF_QULF && blk.rows.some(function(x){ return x.asl; })){
        hr.querySelectorAll('.yb-bolim,.yb-masul').forEach(function(i){ i.disabled = true; });
        var hdb = hr.querySelector('.del-btn'); if (hdb) hdb.remove();
      }
      hr.querySelector('.yb-bolim').addEventListener('input', function(){ blk.bolim = this.value; taklifDirty = true; hisoblaKechik(350); });
      hr.querySelector('.yb-masul').addEventListener('input', function(){ blk.masul = this.value; taklifDirty = true; autoSaqla(); });
      var hdbtn = hr.querySelector('.del-btn');   // qulf (TAKLIF_QULF) blokda tugma yo'q — avval shu yerda JS yiqilib jadval bo'sh qolardi (2026-10-23)
      if (hdbtn) hdbtn.onclick = function(){
        var bor = blk.rows.some(function(x){ return (x.name||'').trim() || x.vol>0; });
        if (bor && !confirm('"'+blk.bolim+'" блокида маълумот бор. Барибир ўчирилсинми?')) return;
        yangiBloklar.splice(bi,1); taklifDirty = true; chizYangiBloklar(); hisobla();
      };
      if (!ichAnchor) ins(hr);
      var blokTrs = [], rqBosh = 0;   // shu blok qatorlarining tr'lari (raqamlash uchun)
      function raqamla(){
        blokTrs.forEach(function(t, i){ t.cells[0].textContent = rqBosh + i + 1; });
      }
      function qatorYasa(x){
        var r = document.createElement('tr'); r.className='extra'; r._ybx = x; r.dataset.bi = bi;
        if (ichAnchor || keyinAnchor) r.className += ' yb-ich';
        var wEdit = tahrirmi();   // hafta ustuni faqat PTO/admin tahriridagi ochiq haftada
        if (TAKLIF_EDIT && x._wkTab !== sel){
          // Taklif/qoralama qatori: tanlangan haftaning saqlangan qiymatlari
          var its0 = wkItems(haftaTop(sel), x.name, blkBolim(blk), x.kind || 'material');
          x.wq = its0 ? its0.qty : 0; x.wp = its0 ? its0.price : 0; x.wi = its0 ? its0.izoh : '';
          x._wkTab = sel;
        }
        var liveQ = wEdit ? (x.wq||0) : 0, liveS = wEdit ? (x.wq||0)*((x.wp||0)||(x.price||0)) : 0;
        r.innerHTML = '<td class="ctr"></td>'+
          '<td><textarea class="wkin tx yb-n" rows="1" placeholder="иш номи..."'+(x.sana ? ' title="Киритилган: '+esc(x.sana)+'"' : '')+'>'+esc(x.name||'')+'</textarea>'+
          (x.sana ? '<span style="font-size:10px; color:#94a3b8; white-space:nowrap; margin-left:4px;">'+esc(x.sana)+'</span>' : '')+
          '<select class="ksel yb-k"><option value="material">Материал</option><option value="labor">Иш ҳақи</option><option value="machinery">Машина части</option><option value="other">Кўзда тутилмаган</option><option value="avans">Аванс</option></select></td>'+
          '<td><textarea class="wkin tx yb-i" rows="1" placeholder="изоҳ...">'+esc(x.izoh||'')+'</textarea></td>'+
          '<td class="ctr"><input class="wkin kichik yb-u" value="'+esc(x.unit||'')+'" placeholder="бирл."></td>'+
          '<td class="num"><input class="wkin yb-q" type="number" min="0" step="0.001" value="'+(x.vol||'')+'"></td>'+
          '<td class="num"><input class="wkin yb-p" type="number" min="0" value="'+(x.price||'')+'"></td>'+
          '<td class="num yb-s">'+fmt((x.vol||0)*(x.price||0))+'</td>'+
          '<td class="num"><input class="wkin yb-bq" type="number" min="0" step="0.001" value="'+(((x.bq||0)+(x.fq||0))||'')+'" placeholder="0" title="Жами бажарилган = ҳафталиклар ('+fmt(x.fq||0)+', автоматик) + қўлда киритилган қисм"></td>'+
          '<td class="num"><input class="wkin yb-bs" type="number" min="0" value="'+(((x.bs||0)+(x.fs||0))||'')+'" placeholder="0" title="Жами бажарилган сумма = ҳафталиклар ('+fmt(x.fs||0)+') + қўлда"></td>'+
          '<td class="num yb-lq">'+fmt((x.vol||0)-(x.fq||0)-(x.bq||0)-liveQ)+'</td><td class="num yb-ls">'+fmt((x.vol||0)*(x.price||0)-(x.fs||0)-(x.bs||0)-liveS)+'</td>'+
          (wEdit
            ? ('<td class="ctr yb-wu">'+esc(x.unit||'')+'</td>'+
               '<td class="num"><span style="display:flex; gap:3px; align-items:flex-start;">'+
               '<input class="wkin yb-wq" type="number" min="0" step="0.001" value="'+(x.wq||'')+'" placeholder="0" style="flex:1; min-width:56px;">'+
               '<button type="button" class="wi-tgl yb-wi-tgl">✎</button>'+
               '<textarea class="wkin tx yb-wi" rows="1" placeholder="ҳафталик изоҳ..." style="display:none; flex:1; min-width:110px;">'+esc(x.wi||'')+'</textarea></span></td>'+
               '<td class="num"><input class="wkin yb-wp" type="number" min="0" value="'+(x.wp||'')+'"></td>'+
               '<td class="num yb-ws">'+fmt((x.wq||0)*(x.wp||0))+'</td>')
            : ((x.wq||0) ? ('<td class="ctr">'+esc(x.unit||'')+'</td><td class="num">'+fmt(x.wq)+'</td><td class="num">'+fmt(x.wp||0)+'</td><td class="num">'+fmt((x.wq||0)*(x.wp||0))+'</td>')
                           : '<td class="ctr dim">—</td><td class="num dim">—</td><td class="num dim">—</td><td class="num dim">—</td>'))+
          '<td class="ctr"><button type="button" class="del-btn" title="Қаторни ўчириш">✕</button></td>';
        r.querySelector('.yb-k').value = x.kind||'material';
        turChipUla(r, r.querySelector('.yb-k'), x);
        if (TAKLIF_QULF && x.asl){
          // Тасдиқланган қатор — қулф: фақат ўқилади (ҳафталик устуни очиқ)
          r.querySelectorAll('.yb-n,.yb-u,.yb-q,.yb-p,.yb-i,.yb-bq,.yb-bs,.yb-k').forEach(function(i){ i.disabled = true; i.title = '🔒 Тасдиқланган қатор — ўзгартириш учун админ рухсати керак'; });
          var db0 = r.querySelector('.del-btn'); if (db0) db0.remove();
        }
        var rdel = r.querySelector('.del-btn'); if (rdel) rdel.onclick = function(){
          if ((x.fq||0) > 0 && !confirm('«'+(x.name||'')+'» бўйича бажарилган иш бор ('+fmt(x.fq)+'). Қатор барибир ўчирилсинми?')) return;
          var i0 = blk.rows.indexOf(x);
          if (i0 > -1) blk.rows.splice(i0, 1);
          taklifDirty = true;
          chizYangiBloklar(); hisobla();
        };
        wiUla(r.querySelector('.yb-wi-tgl'), r.querySelector('.yb-wi'));
        [['yb-n','name'],['yb-u','unit'],['yb-k','kind'],['yb-q','vol'],['yb-p','price'],
         ['yb-i','izoh'],['yb-wq','wq'],['yb-wp','wp'],['yb-wi','wi'],['yb-bq','bq'],['yb-bs','bs']].forEach(function(pr){
          var inp = r.querySelector('.'+pr[0]);
          if (!inp) return;
          inp.addEventListener('input', function(){
            izohKengay(inp);
            var raqam = (pr[1]==='vol'||pr[1]==='price'||pr[1]==='wq'||pr[1]==='wp'||pr[1]==='bq'||pr[1]==='bs');
            x[pr[1]] = raqam ? pf(inp.value) : inp.value;
            taklifDirty = true;
            // Бажарилган катагида ЖАМИ ёзилади — ҳафталиклар айирилиб қўлда қисм сақланади
            if (pr[1]==='bq') x.bq = Math.max(0, x.bq - (x.fq||0));
            if (pr[1]==='bs'){ x.bs = Math.max(0, x.bs - (x.fs||0)); x.bsQolda = true; }
            if ((pr[1]==='bq' || pr[1]==='price') && !x.bsQolda){
              x.bs = Math.round((x.bq||0) * (x.price||0) * 100) / 100;
              var bsI = r.querySelector('.yb-bs'); if (bsI) bsI.value = (x.bs + (x.fs||0)) || '';
            }
            if (pr[1]==='price' && !(x.wp||0)){ var wpI=r.querySelector('.yb-wp'); if (wpI && !wpI.value) wpI.placeholder = inp.value; }
            r.querySelector('.yb-s').textContent = fmt((x.vol||0)*(x.price||0));
            var lq1 = wEdit ? (x.wq||0) : 0, ls1 = wEdit ? (x.wq||0)*((x.wp||0)||(x.price||0)) : 0;
            r.querySelector('.yb-lq').textContent = fmt((x.vol||0)-(x.fq||0)-(x.bq||0)-lq1);
            r.querySelector('.yb-ls').textContent = fmt((x.vol||0)*(x.price||0)-(x.fs||0)-(x.bs||0)-ls1);
            var wu=r.querySelector('.yb-wu'); if (wu) wu.textContent = x.unit||'';
            var ws2=r.querySelector('.yb-ws'); if (ws2) ws2.textContent = fmt((x.wq||0)*((x.wp||x.price)||0));
            // KETMA-KET kiritish: oxirgi qatorga nom yozilishi bilan YANGI qator
            // o'zi qo'shiladi — tanlangan TUR keyingi qatorga meros bo'ladi
            if (pr[1]==='name' && r._turKorinish) r._turKorinish();
            if (pr[1]==='name' && (x.name||'').trim() &&
                blk.rows[blk.rows.length-1] === x){
              var yx = { name:'', unit:'', kind:x.kind||'material', vol:0, price:0 };
              blk.rows.push(yx);
              var nr = qatorYasa(yx);
              r.after(nr);
              raqamla();
            }
            hisoblaKechik(matnMaydonmi(pr[1]) ? 350 : 120);
          });
          inp.addEventListener('change', function(){ hisobla(); });
        });
        blokTrs.push(r);
        izohKengay(r.querySelector('.yb-n'));
        izohKengay(r.querySelector('.yb-i'));
        izohKengay(r.querySelector('.yb-wi'));
        return r;
      }
      if (ichAnchor){
        // Raqamlash mavjud qatorlardan davom etadi
        var t0 = ichAnchor.previousElementSibling;
        while (t0 && !t0.classList.contains('block')){ if (t0.classList.contains('ish') && !rowDel(t0)) rqBosh++; t0 = t0.previousElementSibling; }
        blk.rows.forEach(function(x){ ichAnchor.parentNode.insertBefore(qatorYasa(x), ichAnchor); });
        raqamla();
        return;
      }
      blk.rows.forEach(function(x){ ins(qatorYasa(x)); });
      raqamla();
      // Блок якуни — mavjud bloklardagi kabi (hisobla() to'ldiradi)
      var sub = document.createElement('tr'); sub.className = 'subtotal';
      sub.innerHTML = '<td colspan="6" style="text-align:right;">Блок якуни:</td>'+
        '<td class="num s-total"></td><td></td><td class="num s-fsum"></td>'+
        '<td></td><td class="num s-lsum"></td><td colspan="3"></td>'+
        '<td class="num s-wsum"></td><td></td>';
      ins(sub);
      ybSubs[bi] = sub;
      // Blok YAKUNIDA qo'shish tugmalari: shu blokka qator / yana yangi blok
      var addr = document.createElement('tr'); addr.className = 'yb-addrow';
      addr.innerHTML = '<td style="padding:0;"></td><td colspan="16" style="background:#f6f9fd; padding:1px 8px;">'+
        '<button type="button" class="blk-add yb-addqator" style="margin-left:0; padding:0 8px; font-size:11px; line-height:17px;">+ Қатор</button>'+
        '<button type="button" class="blk-add yb-addblok" style="margin-left:6px; padding:0 8px; font-size:11px; line-height:17px;">+ Янги блок</button></td>';
      addr.querySelector('.yb-addqator').onclick = function(){
        var oxk = blk.rows.length ? (blk.rows[blk.rows.length-1].kind||'material') : 'material';
        blk.rows.push({name:'',unit:'',kind:oxk,vol:0,price:0});
        chizYangiBloklar(); hisobla();
        // shu blokning oxirgi qatoriga fokus
        var qs = document.querySelectorAll('tr.extra[data-bi="'+yangiBloklar.indexOf(blk)+'"] .yb-n');
        if (qs.length) qs[qs.length-1].focus();
      };
      addr.querySelector('.yb-addblok').onclick = function(){
        if (blk.keyin !== undefined) keyinBlokQosh(blk.keyin); else yangiBlokQosh();
      };
      ins(addr);
    });
  }
  function yangiBlokQosh(){
    if (!BLOK_EDIT){ alert('⏳ Лимит сўрови занжирда ёки сизда ҳуқуқ йўқ — ҳозир блок қўшилмайди.'); return; }
    taklifDirty = true;
    yangiBloklar.push({ bolim:'', masul:'',
      rows:[{name:'',unit:'',kind:'material',vol:0,price:0}] });
    chizYangiBloklar(); hisobla();
    var bls = document.querySelectorAll('#yangiBody .yb-bolim');
    if (bls.length) bls[bls.length-1].focus();
  }
  // Mavjud blokdan KEYIN yangi blok (jadvalning o'sha joyida paydo bo'ladi)
  function keyinBlokQosh(bolim0){
    if (!(LIM_EDIT || LIM_QOSH)){ alert('⏳ Лимит сўрови занжирда ёки сизда ҳуқуқ йўқ — ҳозир блок қўшилмайди.'); return; }
    yangiBloklar.push({ bolim:'', masul:'', keyin: bolim0,
      rows:[{name:'',unit:'',kind:'material',vol:0,price:0}] });
    chizYangiBloklar(); hisobla();
    var inp = document.querySelector('#ljBody tr.block[data-bi="'+(yangiBloklar.length-1)+'"] .yb-bolim');
    if (inp) inp.focus();
  }

  function chizBolaklar(tr, key, edit){
    var lst = bolaklar[key] || [];
    var anchor = tr;
    lst.forEach(function(b, i){
      var s = document.createElement('tr');
      s.className = 'split';
      s.dataset.key = key; s.dataset.bi = i;
      s.innerHTML = '<td class="ctr">↳</td>'+
        '<td>'+esc(tr.dataset.name)+' <span style="font-size:11px;">(янги нарх)</span></td>'+
        '<td style="font-size:11.5px; color:#5c2a73;">'+(edit?('<input class="wkin tx bz-i" value="'+esc(b.izoh||'')+'" placeholder="изоҳ">'):esc(b.izoh||''))+'</td>'+
        '<td class="ctr dim">—</td><td class="num dim">—</td><td class="num dim">—</td><td class="num dim">—</td>'+
        '<td class="num dim">—</td><td class="num dim">—</td>'+
        '<td class="num dim">—</td><td class="num dim">—</td>'+
        '<td class="ctr">'+esc(tr.dataset.unit)+'</td>'+
        '<td class="num">'+(edit?('<input class="wkin bz-q" type="number" min="0" step="0.001" value="'+(b.qty||'')+'">'):fmt(b.qty))+'</td>'+
        '<td class="num">'+(edit?('<input class="wkin bz-p" type="number" min="0" value="'+(b.price||'')+'">'):fmt(b.price))+'</td>'+
        '<td class="num bz-s">'+fmt((b.qty||0)*(b.price||0))+'</td>'+
        '<td class="ctr">'+(edit?'<button type="button" class="del-btn" title="Бўлакни ўчириш">✕</button>':'')+'</td>';
      anchor.parentNode.insertBefore(s, anchor.nextSibling);
      anchor = s;
      if (edit){
        var sdel = s.querySelector('.del-btn'); if (sdel) sdel.onclick = function(){
          bolaklar[key].splice(+s.dataset.bi, 1); redrawKey(key);
        };
        ['bz-q','bz-p','bz-i'].forEach(function(cls){
          var inp = s.querySelector('.'+cls);
          if (inp) inp.addEventListener('input', function(){
            var bb = bolaklar[key][+s.dataset.bi];
            if (cls==='bz-q') bb.qty = pf(inp.value);
            if (cls==='bz-p') bb.price = pf(inp.value);
            if (cls==='bz-i') bb.izoh = inp.value;
            s.querySelector('.bz-s').textContent = fmt((bb.qty||0)*(bb.price||0));
            hisoblaKechik(cls==='bz-i' ? 350 : 120);
          });
        });
      }
    });
  }

  function redrawKey(key){
    document.querySelectorAll('#ljBody tr.split').forEach(function(x){
      if (x.dataset.key === key) x.remove();
    });
    var tr = document.querySelector('#ljBody tr.ish[data-key="'+CSS.escape(key)+'"]');
    if (tr) chizBolaklar(tr, key, tahrirmi());
    hisobla();
  }

  // Tur tanlangach select IXCHAM CHIPGA aylanadi; chip bosilsa qayta ochiladi
  var KIND_NOMLARI = { material:'Материал', labor:'Иш ҳақи', machinery:'Машина ч.', other:'Кўзда т.', avans:'Аванс' };
  // «Аванс» qatori: umumiy limit jamiga KIRMAYDI, haftalik jamiga KIRADI (2026-10-23)
  function qatorTuri(tr){ var x = (LIM_EDIT && tr.dataset.id) ? limRows[tr.dataset.id] : null; return (x && x.kind) || tr.dataset.kind || 'material'; }
  function turChipUla(r, sel, x){
    if (!sel) return;
    var chip = document.createElement('span');
    chip.className = 'k-chip';
    chip.title = 'Турни ўзгартириш учун босинг';
    sel.after(chip);
    function korinish(){
      var yashir = (x.name||'').trim() && !r._turOchiq;
      sel.style.display = yashir ? 'none' : '';
      chip.style.display = yashir ? '' : 'none';
      chip.textContent = KIND_NOMLARI[x.kind||'material'] || (x.kind||'');
      chip.className = 'k-chip ' + (x.kind||'material');
    }
    chip.onclick = function(){ r._turOchiq = true; korinish(); sel.focus(); };
    sel.addEventListener('change', function(){ x.kind = sel.value; r._turOchiq = false; korinish(); });
    sel.addEventListener('blur', function(){ r._turOchiq = false; korinish(); });
    r._turKorinish = korinish;
    korinish();
  }

  // -------- 🔍 Излаш + Тури бўйича filtr (birga ishlaydi) --------
  var LJ_KF = 'all';   // tanlangan tur (Барчаси / material / labor / ...)
  function ljQidirla(){
    var qi = document.getElementById('ljQidir');
    var q = (qi && qi.value || '').trim().toLowerCase();
    function matn(t){
      var ichki = Array.prototype.map.call(t.querySelectorAll('input, textarea'),
                                           function(i){ return i.value || ''; }).join(' ');
      return ((t.textContent || '') + ' ' + ichki).toLowerCase();
    }
    ['ljBody', 'yangiBody', 'boshqaBody'].forEach(function(id){
      var tb = document.getElementById(id);
      if (!tb) return;
      var grp = [];
      function flush(){
        if (!grp.length) return;
        // Блок сарлавҳаси (бўлим номи, масъул) мос келса — бутун блок кўринади;
        // акс ҳолда ҚАТОРМА-ҚАТОР: фақат излашга мос келган қаторлар қолади
        var sarl = grp.filter(function(t){ return t.classList.contains('block') || t.classList.contains('exhdr'); });
        var blokOk = !q || sarl.some(function(t){ return matn(t).indexOf(q) > -1; });
        var lastKind = null, lastOk = false, bironBor = false, dataBor = false;
        var vis = grp.map(function(t){
          var chetki = t.classList.contains('block') || t.classList.contains('exhdr') ||
                       t.classList.contains('subtotal') || t.classList.contains('yb-addrow') ||
                       t.classList.contains('lim-addrow');
          if (chetki) return null;
          dataBor = true;
          var s = t.querySelector('.lim-k, .yb-k, .ex-k');
          var k = s ? s.value : (t.dataset.kind || null);
          if (!k) k = lastKind; else lastKind = k;
          var qOk;
          if (t.classList.contains('split')) qOk = lastOk || blokOk || matn(t).indexOf(q) > -1;   // бўлак — ўз қатори билан бирга
          else { qOk = blokOk || matn(t).indexOf(q) > -1; lastOk = qOk; }
          var ok = qOk && (LJ_KF === 'all' || !k || k === LJ_KF);
          if (ok) bironBor = true;
          return ok;
        });
        if (!dataBor) bironBor = blokOk;   // бўш блок — фақат сарлавҳасига қаралади
        grp.forEach(function(t, i){
          if ((t.classList.contains('ish') || t.classList.contains('split')) && rowDelKey(t.dataset.key)){ t.style.display = 'none'; return; }   // ўчирилган қатор
          t.style.display = (vis[i] === null ? bironBor : vis[i]) ? '' : 'none';
        });
        grp = [];
      }
      Array.prototype.forEach.call(tb.rows, function(tr){
        if (tr.classList.contains('block') || tr.classList.contains('exhdr')) flush();
        grp.push(tr);
      });
      flush();
    });
    // Натижа сони — излаш майдони ёнида
    var cnt = document.getElementById('ljQidirN');
    if (cnt){
      if (!q) cnt.textContent = '';
      else {
        var n = 0;
        document.querySelectorAll('#ljBody tr.ish, #yangiBody tr.extra, #boshqaBody tr.extra').forEach(function(t){ if (t.style.display !== 'none') n++; });
        cnt.textContent = n ? (n + ' та натижа') : 'топилмади';
      }
    }
    nomKengayHammasi();
  }
  (function(){
    var qi = document.getElementById('ljQidir');
    if (qi) qi.addEventListener('input', ljQidirla);
    // Излаш майдони: тозалаш тугмаси
    var qWrap = document.getElementById('lhSearch'), qCl = document.getElementById('ljQidirCl');
    if (qi && qWrap){
      var qBor = function(){ qWrap.classList.toggle('bor', !!qi.value); };
      qi.addEventListener('input', qBor); qBor();
      if (qCl) qCl.onclick = function(){ qi.value = ''; qBor(); ljQidirla(); qi.focus(); };
    }
    // Тўлиқ олинган қаторлар — кўрсатиш/яшириш (ҳар объект учун эслаб қолинади)
    // Режимлар: 0 — ҳаммаси, 1 — тўлиқларни яшириш, 2 — ФАҚАТ тўлиқ олинганлар
    var tKor = document.getElementById('lhToliqKor'), tYash = document.getElementById('lhToliqYash'),
        tFaqat = document.getElementById('lhToliqFaqat'), tWrap = document.getElementById('jadvalWrap');
    if (tKor && tYash && tFaqat && tWrap){
      var tKey = 'ljtoliq_' + C.PK + '_' + C.LIM_RAQAM;
      function tQoy(rejim){
        rejim = (rejim === 1 || rejim === 2) ? rejim : 0;
        tWrap.classList.toggle('toliq-yashir', rejim === 1);
        tWrap.classList.toggle('toliq-faqat', rejim === 2);
        tKor.classList.toggle('on', rejim === 0); tYash.classList.toggle('on', rejim === 1); tFaqat.classList.toggle('on', rejim === 2);
        try { localStorage.setItem(tKey, String(rejim)); } catch(e){}
        nomKengayHammasi();
      }
      tKor.onclick = function(){ tQoy(0); };
      tYash.onclick = function(){ tQoy(1); };
      tFaqat.onclick = function(){ tQoy(2); };
      var tR0 = 0; try { tR0 = parseInt(localStorage.getItem(tKey) || '0', 10) || 0; } catch(e){}
      tQoy(tR0);
    }
    // «Жараён қандай ишлайди» — очиш/ёпиш
    var hBtn = document.getElementById('lhHelpBtn'), hBox = document.getElementById('lhHelp');
    if (hBtn && hBox) hBtn.onclick = function(){
      var och = !hBox.classList.contains('show');
      hBox.classList.toggle('show', och); hBtn.setAttribute('aria-expanded', och ? 'true' : 'false');
    };
    var tbar = document.getElementById('ljTurbar');
    if (tbar) tbar.addEventListener('click', function(e){
      var b = e.target.closest('.tchipf');
      if (!b) return;
      LJ_KF = b.dataset.k || 'all';
      tbar.querySelectorAll('.tchipf').forEach(function(x){ x.classList.toggle('on', x === b); });
      ljQidirla();
    });
  })();

  // -------- Лимитда йўқ ишлар (+ Иш) --------
  // Бўлими мавжуд блокка мос келса — қатор ШУ БЛОК ИЧИДА (якунидан олдин)
  // кўринади, акс ҳолда пастдаги «Лимитда йўқ ишлар» бўлимида туради.
  function chizExtralar(){
    document.querySelectorAll('#ljBody tr.extra:not(.yb-ich), #boshqaBody tr.extra, #ljTbl tr.exhdr').forEach(function(x){ x.remove(); });
    var tb = document.getElementById('boshqaBody');
    var edit = tahrirmi();
    if (!extralar.length){ hisobla(); return; }
    var boshqalar = [];
    extralar.forEach(function(x, i){
      var r = document.createElement('tr'); r.className='extra'; r.dataset.ei = i;
      if (edit){
        r.innerHTML = '<td class="ctr">+</td>'+
          '<td><input class="wkin tx ex-n" value="'+esc(x.name||'')+'" placeholder="иш номи...">'+
          '<input class="wkin tx ex-b" list="ljBolimList" value="'+esc(x.bolim||'')+'" placeholder="бўлим (ихтиёрий)" style="margin-top:2px;">'+
          '<select class="ksel ex-k"><option value="material">Материал</option><option value="labor">Иш ҳақи</option><option value="machinery">Машина части</option><option value="other">Кўзда тутилмаган</option><option value="avans">Аванс</option></select></td>'+
          '<td><textarea class="wkin tx ex-i" rows="1" placeholder="изоҳ">'+esc(x.izoh||'')+'</textarea></td>'+
          '<td class="ctr dim">—</td><td class="num dim">—</td><td class="num dim">—</td><td class="num dim">—</td>'+
          '<td class="num dim">—</td><td class="num dim">—</td><td class="num dim">—</td><td class="num dim">—</td>'+
          '<td class="ctr"><input class="wkin kichik ex-u" value="'+esc(x.unit||'')+'" placeholder="бирл."></td>'+
          '<td class="num"><input class="wkin ex-q" type="number" min="0" step="0.001" value="'+(x.qty||'')+'"></td>'+
          '<td class="num"><input class="wkin ex-p" type="number" min="0" value="'+(x.price||'')+'"></td>'+
          '<td class="num ex-s">'+fmt((x.qty||0)*(x.price||0))+'</td>'+
          '<td class="ctr"><button type="button" class="del-btn" title="Ўчириш">✕</button></td>';
        r.querySelector('.ex-k').value = x.kind || 'material';
        turChipUla(r, r.querySelector('.ex-k'), x);
        var rdel = r.querySelector('.del-btn'); if (rdel) rdel.onclick = function(){ extralar.splice(+r.dataset.ei,1); chizExtralar(); hisobla(); };
        [['ex-n','name'],['ex-b','bolim'],['ex-i','izoh'],['ex-u','unit'],['ex-q','qty'],['ex-p','price'],['ex-k','kind']].forEach(function(pr){
          var inp = r.querySelector('.'+pr[0]);
          inp.addEventListener('input', function(){
            izohKengay(inp);
            var xx = extralar[+r.dataset.ei];
            xx[pr[1]] = (pr[1]==='qty'||pr[1]==='price') ? pf(inp.value) : inp.value;
            if (pr[1]==='name' && r._turKorinish) r._turKorinish();
            r.querySelector('.ex-s').textContent = fmt((xx.qty||0)*(xx.price||0));
            hisoblaKechik(matnMaydonmi(pr[1]) ? 350 : 120);
          });
        });
      } else {
        r.innerHTML = '<td class="ctr">+</td>'+
          '<td>'+esc(x.name)+(x.bolim?(' <span style="font-size:10.5px; color:#64748b;">📁 '+esc(x.bolim)+'</span>'):'')+'</td>'+
          '<td style="font-size:11.5px; color:#666;">'+esc(x.izoh||'')+'</td>'+
          '<td class="ctr dim">—</td><td class="num dim">—</td><td class="num dim">—</td><td class="num dim">—</td>'+
          '<td class="num dim">—</td><td class="num dim">—</td><td class="num dim">—</td><td class="num dim">—</td>'+
          '<td class="ctr">'+esc(x.unit)+'</td>'+
          '<td class="num">'+fmt(x.qty)+'</td><td class="num">'+fmt(x.price)+'</td><td class="num">'+fmt((x.qty||0)*(x.price||0))+'</td><td></td>';
      }
      // Mos blok ichiga (yakunidan oldin) joylashtirish
      var joy = null;
      var bnom = (x.bolim||'').trim();
      if (bnom){
        var blk = document.querySelector('#ljBody tr.block[data-bolim="'+CSS.escape(bnom)+'"]');
        if (blk){
          var t = blk.nextElementSibling;
          while (t && !t.classList.contains('subtotal') && !t.classList.contains('block')) t = t.nextElementSibling;
          if (t && t.classList.contains('subtotal')) joy = t;
        }
      }
      if (joy){ joy.parentNode.insertBefore(r, joy); izohKengay(r.querySelector('.ex-i')); }
      else boshqalar.push(r);
    });
    if (boshqalar.length){
      var hr = document.createElement('tr'); hr.className='exhdr block';
      hr.innerHTML = '<td></td><td colspan="14">Лимитда йўқ ишлар (тасдиқлангач умумий лимит рўйхатига қўшилади)</td><td></td>';
      tb.appendChild(hr);
      boshqalar.forEach(function(rr){ tb.appendChild(rr); izohKengay(rr.querySelector('.ex-i')); });
    }
  }
  function ishQosh(bolim){
    if (!tahrirmi()){ alert('🔒 Ёпилган ҳафтага иш қўшилмайди'); return; }
    extralar.push({ name:'', bolim:bolim||'', unit:'', kind:'material', qty:0, price:0, izoh:'' });
    chizExtralar();
    var oxirgi = document.querySelector('tr.extra[data-ei="'+(extralar.length-1)+'"] .ex-n');
    if (oxirgi) oxirgi.focus();
  }

  // -------- ҲИСОБ + ХАТО --------
  function rowWeek(tr){
    var key = tr.dataset.key, q=0, s=0;
    if (tr.dataset.dubl) return { q:0, s:0 };   // бир хил номли 2-қатор — ҳафталик биринчисида
    if (tahrirmi()){
      var iq = tr.querySelector('.in-q'), ip = tr.querySelector('.in-p');
      var mq = iq?pf(iq.value):0, mp = ip?pf(ip.value):0;
      q += mq; s += mq*mp;
      (bolaklar[key]||[]).forEach(function(b){ q+=(b.qty||0); s+=(b.qty||0)*(b.price||0); });
      var wsC = tr.querySelector('.c-ws'); if (wsC && iq) wsC.textContent = fmt(mq*mp);
    } else {
      var h = haftaTop(sel);
      ((h&&h.items[key])||[]).forEach(function(x){ q+=x.qty; s+=x.sum; });
    }
    return { q:q, s:s };
  }

  // YOZISH TEZLIGI (2026-10-23): hisobla() butun jadvalni qayta hisoblaydi — har tugma bosilganda
  // chaqirilsa uzun jadvalda yozish sekinlashadi. Matn maydonlarida yozish to'xtagach ishlaydi.
  var hisoblaT = null;
  function hisoblaKechik(ms){ clearTimeout(hisoblaT); hisoblaT = setTimeout(function(){ hisoblaT = null; hisobla(); }, ms || 120); }
  function matnMaydonmi(k){ return k === 'name' || k === 'izoh' || k === 'unit' || k === 'wi' || k === 'masul' || k === 'bolim'; }
  function setT(el, v){ if (el && el.textContent !== v) el.textContent = v; }   // DOM o'zgarmasa yozmaydi (observer yuki kam)
  function hisobla(){
    if (hisoblaT){ clearTimeout(hisoblaT); hisoblaT = null; }
    var errs = [];
    var warns = [];
    var edit = tahrirmi();
    var gT=0,gF=0,gL=0,gW=0;
    var sT=0,sF=0,sL=0,sW=0,nrk=0;
    var body = document.getElementById('ljBody');
    var toliqN = 0, lastToliq = false;
    Array.prototype.forEach.call(body.rows, function(tr){
      if (tr.classList.contains('block')){ sT=0;sF=0;sL=0;sW=0; nrk=0; lastToliq = false; return; }
      if (tr.classList.contains('split')){ tr.classList.toggle('toliq', lastToliq); return; }
      if (tr.classList.contains('ish')){
        if (rowDel(tr)) return;
        var vol=rowVol(tr), price=rowPrice(tr), fs=pf(tr.dataset.fsum), fq=pf(tr.dataset.fqty);
        lastToliq = vol > 0 && (vol - fq) <= 0.0005;
        tr.classList.toggle('toliq', lastToliq);
        if (lastToliq) toliqN++;
        if (LIM_EDIT && tr.dataset.id){ var lx0 = limRow(tr); fq = pf(tr.dataset.wq) + (lx0.bq||0); fs = pf(tr.dataset.ws) + (lx0.bs||0); }
        var w = rowWeek(tr);
        // Жорий (таҳрирланаётган) ҳафта қиймати Бажарилганга ЖОНЛИ қўшилади
        var fqJ = fq + (edit ? w.q : 0), fsJ = fs + (edit ? w.s : 0);
        // Умумий тахрирланса — Сумма ва Қолган жонли янгиланади
        setT(tr.querySelector('.c-total'), fmt(vol*price));
        if (!LIM_EDIT){ setT(tr.querySelector('.c-fqty'), fmt(fqJ)); setT(tr.querySelector('.c-fsum'), fmt(fsJ)); }
        setT(tr.querySelector('.c-lqty'), fmt(vol-fqJ));
        setT(tr.querySelector('.c-lsum'), fmt(vol*price-fsJ));
        if (qatorTuri(tr) !== 'avans'){ sT+=vol*price; sF+=fsJ; sL+=vol*price-fsJ; gT+=vol*price; gF+=fsJ; gL+=vol*price-fsJ; }
        sW+=w.s; gW+=w.s;
        tr.classList.remove('row-err'); tr.classList.remove('row-warn');
        nrk++; var rn = tr.querySelector('.rn'); setT(rn, nrk);
        // Бажарилган умумийдан ортиқ (лимитдан ошиб бўлган) — ОГОҲЛАНТИРИШ,
        // сақлашни бломайди: бу ЭСКИ тасдиқлар оқибати, янги сўров айби эмас
        if (vol < fq - 0.001){
          warns.push('"'+tr.dataset.name+'"'+(tr.dataset.bolim?(' ('+tr.dataset.bolim+')'):'')+': бажарилган ('+fmt(fq)+') умумийдан ('+fmt(vol)+') ортиқ — лимитдан '+fmt(fq-vol)+' '+tr.dataset.unit+' ошиб бўлган. Умумий объёмни тўғриланг ёки бажарилганни текширинг.');
          tr.classList.add('row-warn');
          setT(rn, nrk+' ⚠');
        }
        if (LIM_EDIT){
          var lx = limRow(tr);
          if (!(lx.name||'').trim()){ errs.push('Умумий қатор: иш номи ўчирилган (бўш) — ёзинг ёки қаторни ✕ билан ўчиринг'); tr.classList.add('row-err'); }
          if (vol < 0 || price < 0){ errs.push('«'+lx.name+'»: объём/нарх манфий бўлмасин'); tr.classList.add('row-err'); }
        }
        if (edit){
          var lq = vol-fq;
          var xatoBor = false;
          if (qatorTuri(tr) === 'avans'){
            // Аванс — СУММА бўйича чегара (бўлиб-бўлиб берилади, миқдор 1 бўлса ҳам)
            var ls = vol*price - fs;
            if (w.s > 0.0001 && w.s > ls + 0.5){
              errs.push('"'+tr.dataset.name+'" (аванс): сўралган сумма '+fmt(w.s)+' — қолган '+fmt(ls)+' дан ортиқ');
              xatoBor = true;
            }
          } else if (w.q > 0.0001 && w.q > lq + 0.001){
            errs.push('"'+tr.dataset.name+'"'+(tr.dataset.bolim?(' ('+tr.dataset.bolim+')'):'')+': сўралган '+fmt(w.q)+' '+tr.dataset.unit+' — қолган '+fmt(lq)+' дан ортиқ');
            xatoBor = true;
          }
          var iq=tr.querySelector('.in-q'), ip=tr.querySelector('.in-p');
          if (iq && pf(iq.value)>0 && ip && pf(ip.value)<=0){ errs.push('"'+tr.dataset.name+'": нарх киритилмаган'); xatoBor = true; }
          (bolaklar[tr.dataset.key]||[]).forEach(function(b){
            if ((b.qty||0)>0 && (b.price||0)<=0){ errs.push('"'+tr.dataset.name+'" (бўлак): нарх киритилмаган'); xatoBor = true; }
          });
          if (xatoBor){ tr.classList.add('row-err'); setT(rn, nrk+' ⛔'); }
        }
        return;
      }
      if (tr.classList.contains('extra')){
        if (tr._ybx){
          // mavjud blok ichiga qo'shilgan YANGI limit qatori — blok yakuniga kiradi
          nrk++; tr.cells[0].textContent = nrk;
          var yx = tr._ybx, ys = (yx.vol||0)*(yx.price||0), yf = (yx.fs||0)+(yx.bs||0)+(edit ? (yx.wq||0)*((yx.wp||0)||(yx.price||0)) : 0);
          sT += ys; sF += yf; sL += ys - yf; sW += (yx.wq||0)*((yx.wp||0)||(yx.price||0));
          return;
        }
        // blok ichiga qo'shilgan yangi ish — blok yakunining hafta summasiga kiradi
        var xx = extralar[+tr.dataset.ei];
        if (xx) sW += (xx.qty||0)*(xx.price||0);
        return;
      }
      if (tr.classList.contains('subtotal')){
        setT(tr.querySelector('.s-total'), fmt(sT));
        setT(tr.querySelector('.s-fsum'), fmt(sF));
        setT(tr.querySelector('.s-lsum'), fmt(sL));
        setT(tr.querySelector('.s-wsum'), fmt(sW));
      }
    });
    // Yangi (saqlanmagan) limit bloklari — Умумий ва Қолганга qo'shiladi
    yangiBloklar.forEach(function(blk, bi){
      var blkNomi = blkBolim(blk).trim() || (TAKLIF_EDIT ? 'Бўлимсиз' : 'Янги блок');
      var toldirilgan = false;
      var bT=0, bF=0, bW=0;
      var wEditB = tahrirmi();
      blk.rows.forEach(function(x){
        var wnarx = (x.wp||0) || (x.price||0);
        // joriy hafta — Бажарилганга jonli
        var s0 = (x.vol||0)*(x.price||0), f0 = (x.fs||0) + (x.bs||0) + (wEditB ? (x.wq||0)*wnarx : 0),
            fq0 = (x.fq||0) + (x.bq||0) + (wEditB ? (x.wq||0) : 0);
        if ((x.kind||'material') !== 'avans'){ gT += s0; gF += f0; gL += s0 - f0; bT += s0; bF += f0; }
        gW += (x.wq||0) * wnarx;
        bW += (x.wq||0) * wnarx;
        if ((x.name||'').trim() && (x.vol||0) < fq0 - 0.001)
          warns.push('"'+x.name+'"'+(blk.bolim?(' ('+blk.bolim+')'):'')+': бажарилган ('+fmt(fq0)+') умумийдан ('+fmt(x.vol||0)+') ортиқ.');
        if ((x.bq||0) < 0 || (x.bs||0) < 0) errs.push('«'+(x.name||blkNomi)+'»: бажарилган қисми манфий бўлмасин');
        if ((x.vol||0)>0 || (x.wq||0)>0 || (x.name||'').trim()) toldirilgan = true;
        if ((x.vol||0) < 0 || (x.price||0) < 0)
          errs.push('«'+(x.name||blkNomi)+'»: объём/нарх манфий бўлмасин');
        if (((x.vol||0)>0 || (x.wq||0)>0) && !(x.name||'').trim())
          errs.push('«'+blkNomi+'» блоки: иш номи ёзилмаган');
        // Умумий НАРХ мажбурий ЭМАС — занжирда СНАБЖЕНИЕ нархлайди
        if ((x.wq||0)>0 && wnarx<=0) errs.push('«'+(x.name||blkNomi)+'» (ҳафталик): нарх киритилмаган');
        if ((x.wq||0)>0 && (x.vol||0)>0){
          if ((x.kind||'material') === 'avans'){
            if ((x.wq||0)*wnarx > (x.vol||0)*(x.price||0)+0.5) errs.push('«'+(x.name||blkNomi)+'» (аванс): ҳафталик сумма '+fmt((x.wq||0)*wnarx)+' — умумий '+fmt((x.vol||0)*(x.price||0))+' дан ортиқ');
          } else if ((x.wq||0) > (x.vol||0)+0.001)
            errs.push('«'+(x.name||blkNomi)+'»: ҳафталик '+fmt(x.wq)+' — умумий '+fmt(x.vol)+' дан ортиқ');
        }
      });
      // Taklif tahririda bo'limsiz blok ruxsat («Бўлимсиз қаторлар» — eski ma'lumot)
      if (toldirilgan && !blkBolim(blk).trim() && !TAKLIF_EDIT && blk.ichida === undefined)
        errs.push('Янги блокнинг БЎЛИМ НОМИ ёзилмаган (сариқ қатордаги биринчи катак)');
      var sb2 = ybSubs[bi];
      if (sb2){
        sb2.querySelector('.s-total').textContent = fmt(bT);
        sb2.querySelector('.s-fsum').textContent = fmt(bF);
        sb2.querySelector('.s-lsum').textContent = fmt(bT-bF);
        sb2.querySelector('.s-wsum').textContent = fmt(bW);
      }
    });
    extralar.forEach(function(x, i){
      gW += (x.qty||0)*(x.price||0);
      if (edit){
        var r = document.querySelector('tr.extra[data-ei="'+i+'"]');
        if (r) r.classList.remove('row-err');
        var xb = false;
        if ((x.qty||0)>0 && !(x.name||'').trim()){ errs.push('Лимитда йўқ иш: номи ёзилмаган'); xb=true; }
        if ((x.qty||0)>0 && (x.price||0)<=0){ errs.push('"'+(x.name||'янги иш')+'": нарх киритилмаган'); xb=true; }
        if (xb && r) r.classList.add('row-err');
      }
    });
    var tN = document.getElementById('lhToliqN'); if (tN) setT(tN, toliqN);
    (function(){   // блокда тўлиқ олинган қатор йўқ бўлса — «Фақат тўлиқ» режимида блок сарлавҳаси ва якуни ҳам яширилади
      var blk = [], bor = false;
      function yop(){ blk.forEach(function(t){ t.classList.toggle('toliq-yoq', !bor); }); blk = []; bor = false; }
      Array.prototype.forEach.call(body.rows, function(tr){
        if (tr.classList.contains('block')){ yop(); blk.push(tr); return; }
        if (tr.classList.contains('subtotal')){ blk.push(tr); return; }
        if (tr.classList.contains('ish') && tr.classList.contains('toliq') && !rowDel(tr)) bor = true;
      });
      yop();
      var wrp = document.getElementById('jadvalWrap'); if (wrp) wrp.classList.toggle('toliq-nol', toliqN === 0);
    })();
    setT(document.getElementById('gTotal'), fmt(gT));
    setT(document.getElementById('gFsum'), fmt(gF));
    setT(document.getElementById('gLsum'), fmt(gL));
    setT(document.getElementById('gWsum'), fmt(gW));
    var panel = document.getElementById('ljErr'), list = document.getElementById('ljErrList'), ok = document.getElementById('ljOk');
    list.innerHTML='';
    // --- Бир блокда бир хил номли қаторлар ---
    (function(){
      var sanoq = {}, nomlar = {}, tahrir = {};
      function qosh(nom, bolim, kind, tahrirlanadi){
        var k = wkKey(nom, bolim, kind);
        if (!(nom||'').trim()) return;
        sanoq[k] = (sanoq[k]||0) + 1; nomlar[k] = [nom, bolim]; if (tahrirlanadi) tahrir[k] = true;
      }
      ishRows().forEach(function(tr){
        if (rowDel(tr)) return;
        if (LIM_EDIT && tr.dataset.id){ var lx = limRow(tr); qosh(lx.name, tr.dataset.bolim, lx.kind || tr.dataset.kind, true); }
        else qosh(tr.dataset.name, tr.dataset.bolim, tr.dataset.kind, false);
      });
      yangiBloklar.forEach(function(blk){ blk.rows.forEach(function(x){ qosh(x.name, blkBolim(blk), x.kind || 'material', true); }); });
      Object.keys(sanoq).forEach(function(k){
        if (sanoq[k] < 2) return;
        var n = nomlar[k], matn = '«'+n[0]+'» номи «'+(n[1]||'Бўлимсиз')+'» блокида '+sanoq[k]+' марта — ҳафталик фақат биринчи қаторга ёзилади. Бошқа иш бўлса номини фарқланг (масалан: «'+n[0]+' — дюсел 150»).';
        if (tahrir[k]) errs.push(matn); else warns.push(matn);
      });
    })();
    window._ljErr = errs.length>0;
    if (errs.length){
      setT(document.getElementById('ljErrHdr'), '⛔ '+errs.length+' та хато');
      errs.forEach(function(e){ var li=document.createElement('li'); li.textContent=e; list.appendChild(li); });
      panel.classList.add('show'); ok.classList.remove('show');
    } else { panel.classList.remove('show'); if ((edit || TAKLIF_EDIT) && !warns.length) ok.classList.add('show'); else ok.classList.remove('show'); }
    // Огоҳлантиришлар (сариқ) — сақлашни БЛОКЛАМАЙДИ
    var wp = document.getElementById('ljWarn'), wl = document.getElementById('ljWarnList');
    if (wp){
      wl.innerHTML='';
      if (warns.length){
        setT(document.getElementById('ljWarnHdr'), '⚠ '+warns.length+' та огоҳлантириш (лимитдан ошиб бўлган)');
        warns.forEach(function(e){ var li=document.createElement('li'); li.textContent=e; wl.appendChild(li); });
        wp.classList.add('show');
      } else wp.classList.remove('show');
    }
    var sb = document.getElementById('sanaBox');
    if (sb) sb.style.display = edit ? 'flex' : 'none';
    var hR = haftaTop(sel), ruxsatRejim = !!(hR && hR.ruxsat && hR.status !== 'draft');
    ['btnSaqla','btnYopish'].forEach(function(id){
      var b = document.getElementById(id); if (!b) return;
      var mumkin = edit && !(id==='btnYopish' && ruxsatRejim);
      b.disabled = !mumkin;
      b.title = mumkin ? (id==='btnSaqla' ? 'Ҳафта қоралама бўлиб сизда қолади — кейинги юсерга ўтмайди' : 'Ҳафта ёпилади ва занжирга кетади')
                       : (ruxsatRejim ? 'Рухсат режимида фақат «Сақлаш» — ҳафта ҳолати ўзгармайди' : 'Бу ҳафта қулф — янги ҳафта учун «+ Янги ҳафта» табини танланг');
    });
    var wkHint = document.getElementById('wkFtHint');
    if (wkHint) wkHint.textContent = edit
      ? (ruxsatRejim ? 'Рухсат бир марталик: «Сақлаш» — ўзгаришлар ёзилади, ҳафта ҳолати ўзгармайди, рухсат ёпилади.'
                     : 'Сақлаш: ҳафта қоралама бўлиб сизда қолади (кейинги юсерга ўтмайди). Юбориш: ҳафта ёпилади ва занжирга кетади — снабжения → ПТО хулосаси → директор → проверчик → админ.')
      : 'Бу ҳафта қулф. Янги ҳафта киритиш учун «+ Янги ҳафта» табини танланг.';
    document.querySelectorAll('#ljBody tr.lim-addrow').forEach(function(b){ b.style.display = (LIM_EDIT || LIM_QOSH) ? '' : 'none'; });
    var bLim0 = document.getElementById('btnLimYubor');
    if (bLim0) bLim0.style.display = (LIM_EDIT || LIM_QOSH) ? '' : 'none';
    var bLimZ0 = document.getElementById('btnLimZanjir');
    if (bLimZ0) bLimZ0.style.display = LIM_EDIT ? '' : 'none';
    hammaRaqamKengay();
    ljQidirla();
    autoSaqla();
  }

  document.getElementById('ljBody').addEventListener('keydown', function(e){
    if (e.key === 'Enter' && (e.target.classList.contains('lim-n') || e.target.classList.contains('yb-n'))) e.preventDefault();
  });
  document.getElementById('ljBody').addEventListener('input', function(e){
    var c = e.target.classList;
    if (c.contains('lim-n') || c.contains('yb-n')){ if (e.target.value.indexOf('\n') >= 0) e.target.value = e.target.value.replace(/\n/g, ' '); izohKengay(e.target); }
    if (c.contains('in-i') || c.contains('ex-i') || c.contains('lim-i')) izohKengay(e.target);
    var tr = e.target.closest('tr.ish');
    if (tr && LIM_EDIT && tr.dataset.id){
      var x = limRow(tr);
      if (c.contains('lim-n')){ x.name = e.target.value; if (tr._turKorinish) tr._turKorinish();
        if (pf(tr.dataset.fqty) > 0 && !tr._nomOgoh){ tr._nomOgoh = true; alert('Бу қатор бўйича бажарилган иш бор. Номини ўзгартирсангиз, ҳафталик тарихи янги номга ўтказилади.'); } }
      else if (c.contains('lim-u')){ x.unit = e.target.value; tr.querySelector('.c-wb').textContent = x.unit; }
      else if (c.contains('lim-i')) x.izoh = e.target.value;
      else if (c.contains('lim-k')) x.kind = e.target.value;
      else if (c.contains('lim-q')) x.vol = pf(e.target.value);
      else if (c.contains('lim-p')) x.price = pf(e.target.value);
      else if (c.contains('lim-bq')){
        // Yozilgan son — ЖАМИ; haftaliklar (server) ayirilib qo'lda qism saqlanadi
        x.bq = Math.max(0, pf(e.target.value) - pf(tr.dataset.wq));
        if (!x.bsQolda){ x.bs = Math.round(x.bq * (x.price||0) * 100) / 100; var bsI = tr.querySelector('.lim-bs'); if (bsI) bsI.value = (x.bs + pf(tr.dataset.ws)) || ''; } }
      else if (c.contains('lim-bs')){ x.bs = Math.max(0, pf(e.target.value) - pf(tr.dataset.ws)); x.bsQolda = true; }
    }
    if (c.contains('lim-n') || c.contains('lim-u') || c.contains('lim-i') || c.contains('in-i')) hisoblaKechik(350);
    else if (c.contains('in-q') || c.contains('in-p') || c.contains('lim-q') || c.contains('lim-p') ||
        c.contains('lim-k') || c.contains('lim-bq') || c.contains('lim-bs')) hisoblaKechik(120);
  });
  document.getElementById('ljBody').addEventListener('change', function(e){
    var c = e.target.classList;
    if (c.contains('lim-n') || c.contains('lim-u') || c.contains('lim-i') || c.contains('in-i') || c.contains('in-q') || c.contains('in-p') ||
        c.contains('lim-q') || c.contains('lim-p') || c.contains('lim-k') || c.contains('lim-bq') || c.contains('lim-bs')) hisobla();
  });
  // Mavjud blok ichiga «+ Қатор» — янги қатор ШУ блокнинг охирида пайдо бўлади
  document.getElementById('ljBody').addEventListener('click', function(e){
    if (e.target.classList.contains('lim-addblok')){ keyinBlokQosh(e.target.closest('tr.lim-addrow').dataset.bolim); return; }
    if (!e.target.classList.contains('lim-addqator')) return;
    if (!(LIM_EDIT || LIM_QOSH)){ alert('⏳ Лимит сўрови занжирда ёки сизда ҳуқуқ йўқ — ҳозир қатор қўшилмайди.'); return; }
    var bolim0 = e.target.closest('tr.lim-addrow').dataset.bolim;
    var blk = null;
    yangiBloklar.forEach(function(b){ if (b.ichida === bolim0) blk = b; });
    if (!blk){ blk = { ichida: bolim0, bolim:'', masul:'', rows:[] }; yangiBloklar.push(blk); }
    // tur — shu blokning oxirgi qatoridan meros
    var oxk = 'material';
    if (blk.rows.length) oxk = blk.rows[blk.rows.length-1].kind || 'material';
    else { var t0 = e.target.closest('tr.lim-addrow').previousElementSibling;
           while (t0 && !t0.classList.contains('block')){ if (t0.classList.contains('ish')){ oxk = (limRows[t0.dataset.id]||{}).kind || t0.dataset.kind || 'material'; break; } t0 = t0.previousElementSibling; } }
    blk.rows.push({name:'', unit:'', kind:oxk, vol:0, price:0});
    chizYangiBloklar(); hisobla();
    var qs = document.querySelectorAll('#ljBody tr.lim-addrow[data-bolim="'+CSS.escape(bolim0)+'"]');
    var prev = qs.length ? qs[0].previousElementSibling : null;
    if (prev && prev.querySelector('.yb-n')) prev.querySelector('.yb-n').focus();
  });

  // -------- ✂ БЎЛИШ --------
  var modalKey = null;
  function ochModal(key){
    modalKey = key;
    var tr = document.querySelector('#ljBody tr.ish[data-key="'+CSS.escape(key)+'"]');
    var iq = tr.querySelector('.in-q'), ip = tr.querySelector('.in-p');
    var q = iq?pf(iq.value):0, p = ip?pf(ip.value):0;
    document.getElementById('mInfo').innerHTML = '<b>'+esc(tr.dataset.name)+'</b> — ҳафталик режа: <b>'+fmt(q)+' '+esc(tr.dataset.unit)+'</b>, жорий нарх: <b>'+fmt(p)+'</b> сўм.<br>Эски нархда сўраладиган қисмни ёзинг — қолгани янги нархда алоҳида қатор бўлади.';
    document.getElementById('mEski').value=''; document.getElementById('mEski').max=q;
    document.getElementById('mYangi').value=''; document.getElementById('mIzoh').value='';
    document.getElementById('mPreview').textContent='—';
    document.getElementById('ljModalBg').classList.add('show');
  }
  function mPreview(){
    var tr = document.querySelector('#ljBody tr.ish[data-key="'+CSS.escape(modalKey)+'"]');
    var iq = tr.querySelector('.in-q'), ip = tr.querySelector('.in-p');
    var q = iq?pf(iq.value):0, p = ip?pf(ip.value):0;
    var eski = pf(document.getElementById('mEski').value), yangi = pf(document.getElementById('mYangi').value);
    if (eski<0 || eski>q || !yangi){ document.getElementById('mPreview').textContent='—'; return; }
    var qoldi = Math.round((q-eski)*1000)/1000;
    document.getElementById('mPreview').innerHTML =
      'Натижа:<br>&bull; '+fmt(eski)+' '+esc(tr.dataset.unit)+' × '+fmt(p)+' = <b>'+fmt(eski*p)+'</b> сўм (эски нарх)<br>'+
      '&bull; '+fmt(qoldi)+' '+esc(tr.dataset.unit)+' × '+fmt(yangi)+' = <b>'+fmt(qoldi*yangi)+'</b> сўм (янги нарх)<br>'+
      'Жами: <b>'+fmt(eski*p+qoldi*yangi)+'</b> сўм';
  }
  if (CAN_EDIT){
    document.getElementById('mEski').addEventListener('input', mPreview);
    document.getElementById('mYangi').addEventListener('input', mPreview);
    document.getElementById('mBekor').onclick = function(){ document.getElementById('ljModalBg').classList.remove('show'); };
    document.getElementById('mBol').onclick = function(){
      var tr = document.querySelector('#ljBody tr.ish[data-key="'+CSS.escape(modalKey)+'"]');
      var iq = tr.querySelector('.in-q');
      var q = iq?pf(iq.value):0;
      var eski = pf(document.getElementById('mEski').value), yangi = pf(document.getElementById('mYangi').value);
      if (eski<0 || eski>q){ alert('Эски объём 0 дан '+fmt(q)+' гача бўлиши керак'); return; }
      if (!yangi){ alert('Янги нархни киритинг'); return; }
      var qoldi = Math.round((q-eski)*1000)/1000;
      iq.value = eski;
      (bolaklar[modalKey] = bolaklar[modalKey]||[]).push({ qty:qoldi, price:yangi,
        izoh:document.getElementById('mIzoh').value||'янги нарх (бўлинган)' });
      document.getElementById('ljModalBg').classList.remove('show');
      redrawKey(modalKey);
    };
  }

  // -------- САҚЛАШ / ҲАФТАНИ ЁПИШ --------
  var limMode = 'qoralama';   // 'qoralama' (Сақлаш) | 'yubor' (▶ Лимитни юбориш/қўллаш) | 'zanjir'
  function yuborish(action){
    hisobla();
    if (window._ljErr){ alert('⛔ Хато бор — аввал тўғриланг (юқоридаги рўйхат)'); return; }
    var items = [];
    ishRows().forEach(function(tr){
      if (rowDel(tr)) return;   // ўчирилган қаторнинг ҳафталиги ҳам кетмайди
      if (tr.dataset.dubl) return;   // бир хил номли 2-қатор — ҳафталиги биринчи қаторда
      var iq = tr.querySelector('.in-q'), ip = tr.querySelector('.in-p'), ii = tr.querySelector('.in-i');
      if (!iq) return;
      var q = pf(iq.value);
      if (q>0) items.push({ name:tr.dataset.name, bolim:tr.dataset.bolim, unit:tr.dataset.unit,
                            kind:tr.dataset.kind, qty:q, price:pf(ip.value), izoh: ii?ii.value:'' });
      (bolaklar[tr.dataset.key]||[]).forEach(function(b){
        if ((b.qty||0)>0) items.push({ name:tr.dataset.name, bolim:tr.dataset.bolim, unit:tr.dataset.unit,
                                       kind:tr.dataset.kind, qty:b.qty, price:b.price, izoh:b.izoh||'' });
      });
    });
    extralar.forEach(function(x){
      if ((x.qty||0)>0 && (x.name||'').trim())
        items.push({ name:x.name, bolim:x.bolim, unit:x.unit, kind:x.kind||'material',
                     qty:x.qty, price:x.price, izoh:x.izoh||'' });
    });
    // Yangi blok qatorlarining HAFTALIK ustuni ham haftalik so'rovga kiradi
    yangiBloklar.forEach(function(blk){
      blk.rows.forEach(function(x){
        if ((x.wq||0)>0 && (x.name||'').trim())
          items.push({ name:x.name, bolim:blkBolim(blk).trim(), unit:x.unit||'',
                       kind:x.kind||'material', qty:x.wq,
                       price:(x.wp||0) || (x.price||0),
                       izoh:(x.wi||'').trim() || x.izoh || '' });
      });
    });
    // Умумий таҳрирлари: ўзгарган/ўчирилган мавжуд қаторлар + янги қаторлар
    var lp0 = limPayload();
    var limEdits = lp0.edits, limNew = lp0.yangi, limDel = lp0.del;
    var lsQ = document.getElementById('limStart'), leQ = document.getElementById('limEnd');
    var sanaOzgardi = !!(lsQ && leQ && (lsQ.value !== LIM_SANA0[0] || leQ.value !== LIM_SANA0[1]));
    var limBor = !!(limEdits.length || limNew.length || limDel.length);
    if (!items.length && !limBor && !sanaOzgardi){ limZanjir=false; limMode='qoralama'; alert('Камида битта қаторга объём киритинг.'); return; }
    if (limMode !== 'qoralama' && (!ADM_TOGRI || limZanjir) && limBor &&
        !confirm('Умумий лимит ўзгаришлари СНАБЖЕНИЕ → ПТО → ДИРЕКТОР → ПРОВЕРЧИК → АДМИН занжирига сўров бўлиб кетади ва Тасдиқлар бўлимида кўринади. Давом этамизми?')){ limZanjir=false; limMode='qoralama'; return; }
    if (limMode === 'qoralama' && limBor &&
        !confirm((QORALAMA_BOR && !TAKLIF_EDIT ? 'ДИҚҚАТ: бу объектда сақланган ҚОРАЛАМА бор — у ҳозирги жадвал ҳолати билан АЛМАШТИРИЛАДИ.\n\n' : '')+
                 'Умумий лимит ўзгаришлари ҚОРАЛАМА сифатида сақланади (занжирга юборилмайди) — кейин «📝 Қораламани очиш» орқали давом этасиз. Юбориш учун «▶ Лимитни юбориш»ни босасиз. Давом этамизми?')){ return; }
    if (!items.length && action==='submit'){ alert('Ҳафтани ёпиш учун ҳафталик устунига объём киритинг.'); return; }
    var h = haftaTop(sel);
    var wsEl = document.getElementById('wkStart'), weEl = document.getElementById('wkEnd');
    var lsEl = document.getElementById('limStart'), leEl = document.getElementById('limEnd');
    var payload = { action:action, items:items, limit_edits:limEdits, limit_new:limNew, limit_del:limDel,
      hafta_id: (h && h.ruxsat && h.status !== 'draft') ? h.id : null,
      lim_zanjir: limZanjir, lim_qoralama: limMode === 'qoralama',
      week_start: (wsEl ? wsEl.value : '') || (h?h.ws:''),
      week_end: (weEl ? weEl.value : '') || (h?h.we:'') };
    if (lsEl && leEl){ payload.lim_start = lsEl.value || ''; payload.lim_end = leEl.value || ''; }
    if (TAKLIF_EDIT){
      // Taklif/qoralama tahririda: taklif qatorlari ham shu POST bilan saqlanadi
      var tq = taklifQatorlar();
      if (!tq.length){ alert('Камида битта таклиф қатори бўлиши керак.'); return; }
      payload.req_items = tq; payload.token = TAKLIF_TOKEN; taklifDirty = false;
    }
    limZanjir = false; limMode = 'qoralama';
    if (action==='submit' && !confirm('Ҳафта ёпилади — сўров тасдиқлаш занжирига (снабжения → ПТО хулосаси → директор → проверчик → админ) юборилади. Давом этамизми?')) return;
    // Forma ketgach kechikkan avtosaqlash eski holatni QAYTA yozib qo'ymasin
    yuborilmoqda = true; clearTimeout(autoT);
    try { localStorage.removeItem(autoKey()); } catch(e){}
    document.getElementById('ljPayload').value = JSON.stringify(payload);
    document.getElementById('ljForm').submit();
  }
  // -------- 💾 ТАКЛИФНИ САҚЛАШ (занжирдаги сўров қаторлари) --------
  function taklifQatorlar(){
    var rows = [];
    yangiBloklar.forEach(function(blk){
      blk.rows.forEach(function(x){
        if ((x.name||'').trim())
          rows.push({ bolim:(blk.bolim||'').trim(), masul:(blk.masul||'').trim(), name:x.name,
                      unit:x.unit||'', kind:x.kind||'material', vol:x.vol||0, price:x.price||0,
                      note:x.izoh||'', asl:x.asl||null, bq:x.bq||0, bs:x.bs||0 });
      });
    });
    return rows;
  }
  function taklifSaqla(){
    hisobla();
    if (window._ljErr){ alert('⛔ Хато бор — аввал тўғриланг (юқоридаги рўйхат)'); return; }
    var rows = [];
    yangiBloklar.forEach(function(blk){
      blk.rows.forEach(function(x){
        if ((x.name||'').trim())
          rows.push({ bolim:(blk.bolim||'').trim(), masul:(blk.masul||'').trim(), name:x.name,
                      unit:x.unit||'', kind:x.kind||'material', vol:x.vol||0, price:x.price||0,
                      note:x.izoh||'', asl:x.asl||null, bq:x.bq||0, bs:x.bs||0 });
      });
    });
    if (!rows.length){ alert('Камида битта қатор бўлиши керак. Сўровни бутунлай бекор қилиш учун уни орқага қайтаринг ёки рад этинг.'); return; }
    yuborilmoqda = true; taklifDirty = false;
    document.getElementById('ljPayload').value = JSON.stringify({ action:'req_save', token:TAKLIF_TOKEN, req_items:rows });
    document.getElementById('ljForm').submit();
  }
  var bRS = document.getElementById('btnReqSaqla');
  if (bRS) bRS.onclick = taklifSaqla;
  // -------- 💾 ҚОРАЛАМА САҚЛАШ (Умумий тўлиқ рўйхати серверга, занжирсиз) --------
  var bQ = document.getElementById('btnQoralama');
  if (bQ) bQ.onclick = function(){
    hisobla();
    if (window._ljErr){ alert('⛔ Хато бор — аввал тўғриланг (юқоридаги рўйхат)'); return; }
    var rows = [];
    ishRows().forEach(function(tr){
      if (rowDel(tr) || !tr.dataset.id) return;
      var x = limRow(tr), b = limBlok(tr.dataset.bolim);
      if (!(x.name||'').trim()) return;
      rows.push({ asl:+tr.dataset.id, name:x.name, unit:x.unit||'', kind:x.kind||'material', note:x.izoh||'',
                  bolim:(b.bolim||'').trim(), masul:(b.masul||'').trim(), vol:x.vol||0, price:x.price||0, bq:x.bq||0, bs:x.bs||0 });
    });
    yangiBloklar.forEach(function(blk){
      blk.rows.forEach(function(x){
        if ((x.name||'').trim())
          rows.push({ asl:null, name:x.name, unit:x.unit||'', kind:x.kind||'material', note:x.izoh||'',
                      bolim:blkBolim(blk).trim(), masul:blkMasul(blk).trim(), vol:x.vol||0, price:x.price||0, bq:x.bq||0, bs:x.bs||0 });
      });
    });
    if (!rows.length){ alert('Қоралама учун камида битта қатор (иш номи) киритинг.'); return; }
    if (!confirm('Жадвал ҚОРАЛАМА сифатида сақланади (занжирга юборилмайди). Кейин шу жойидан давом этасиз. Давом этамизми?')) return;
    yuborilmoqda = true; clearTimeout(autoT);
    try { localStorage.removeItem(autoKey()); } catch(e){}
    document.getElementById('ljPayload').value = JSON.stringify({ action:'draft_save', req_items:rows });
    document.getElementById('ljForm').submit();
  };
  if (TAKLIF_EDIT){
    // Saqlanmagan tahrir bilan bosqichni o'tkazib yubormaslik
    document.querySelectorAll('form.lim-amal-f').forEach(function(f){
      f.addEventListener('submit', function(e){
        if (taklifDirty){ e.preventDefault(); e.stopImmediatePropagation();
          alert('Таклифда САҚЛАНМАГАН ўзгариш бор — аввал «💾 Таклифни сақлаш»ни босинг.'); }
      }, true);
    });
    window.addEventListener('beforeunload', function(e){
      if (taklifDirty && !yuborilmoqda){ e.preventDefault(); e.returnValue = ''; }
    });
  }
  var bS = document.getElementById('btnSaqla'), bY = document.getElementById('btnYopish');
  if (bS) bS.onclick = function(){ yuborish('save'); };
  if (bY) bY.onclick = function(){ yuborish('submit'); };
  // ▶ Лимитни юбориш — Умумий ўзгаришларини кейинги юсерга (занжирга) жўнатиш
  var limZanjir = false;   // admin «Занжирга юбориш»ни танласа true bo'ladi
  var LIM_SANA0 = [ (document.getElementById('limStart')||{}).value || '', (document.getElementById('limEnd')||{}).value || '' ];
  // Умумий ўзгаришлари: {edits:[{id,...}], del:[id], yangi:[...]} — фақат ҲАҚИҚИЙ ўзгарганлар
  function limPayload(){
    var out = { edits:[], del:[], yangi:[] };
    if (!LIM_EDIT && !LIM_QOSH) return out;
    if (LIM_EDIT) ishRows().forEach(function(tr){
      var id = tr.dataset.id; if (!id) return;
      var x = limRows[id]; if (!x) return;
      if (x.del){ out.del.push(+id); return; }
      var b = limBlok(tr.dataset.bolim);
      var e = { id:+id, name:(x.name||'').trim(), unit:(x.unit||'').trim(), kind:x.kind||'material', note:(x.izoh||'').trim(),
                bolim:(b.bolim||'').trim(), masul:(b.masul||'').trim(), vol:x.vol||0, price:x.price||0,
                bq:x.bq||0, bs:x.bs||0 };
      var asl = { name:tr.dataset.name, unit:tr.dataset.unit, kind:tr.dataset.kind||'material', note:tr.dataset.izoh||'',
                  bolim:tr.dataset.bolim, masul:(document.querySelector('#ljBody tr.block[data-bolim="'+CSS.escape(tr.dataset.bolim)+'"]')||{dataset:{}}).dataset.masul||'' };
      var ozg = Math.abs(e.vol-pf(tr.dataset.vol))>0.0001 || Math.abs(e.price-pf(tr.dataset.price))>0.0001 ||
                Math.abs(e.bq-pf(tr.dataset.bq))>0.0001 || Math.abs(e.bs-pf(tr.dataset.bs))>0.001;
      Object.keys(asl).forEach(function(k){ if (e[k] !== asl[k]) ozg = true; });
      if (ozg) out.edits.push(e);
    });
    yangiBloklar.forEach(function(blk){
      blk.rows.forEach(function(x){
        if ((x.name||'').trim())
          out.yangi.push({ bolim:blkBolim(blk), masul:blkMasul(blk), name:x.name, unit:x.unit||'',
                           kind:x.kind||'material', vol:x.vol||0, price:x.price||0, note:x.izoh||'', asl:x.asl||null,
                           bq:x.bq||0, bs:x.bs||0 });
      });
    });
    return out;
  }
  function limOzgarishBor(){
    var lsE = document.getElementById('limStart'), leE = document.getElementById('limEnd');
    if (lsE && leE && (lsE.value !== LIM_SANA0[0] || leE.value !== LIM_SANA0[1])) return true;
    var lp = limPayload();
    return !!(lp.edits.length || lp.del.length || lp.yangi.length);
  }
  var bLimZ = document.getElementById('btnLimZanjir');
  if (bLimZ) bLimZ.onclick = function(){
    if (!limOzgarishBor()){
      alert('Умумий устунда ўзгариш йўқ.\nАввал Умумий объём/нархни ўзгартиринг ёки «+ Блок (бўлим)» билан янги блок киритинг — кейин занжирга юборасиз.');
      return;
    }
    limZanjir = true; limMode = 'zanjir';
    yuborish('save');
  };
  var bLim = document.getElementById('btnLimYubor');
  if (bLim) bLim.onclick = function(){
    var lp = limPayload();
    if (!(lp.edits.length || lp.del.length || lp.yangi.length)){
      alert('Умумий устунда ўзгариш йўқ.\nАввал Умумий объём/нархни ўзгартиринг ёки «+ Блок (бўлим)» билан янги блок киритинг — кейин шу тугма билан юборасиз.');
      return;
    }
    limMode = 'yubor';
    yuborish('save');
  };

  // -------- 📊 НАРХ ТАРИХИ --------
  document.getElementById('btnExcel').onclick = function(){
    // Tanlangan hafta bilan birga yuklab olish ('yangi' — hali saqlanmagan, haftasiz)
    var hid = '';
    if (sel !== 'yangi') hid = sel.slice(1);
    else if (DRAFT_ID !== null) hid = DRAFT_ID;
    location.href = EXPORT_URL + (hid ? ('&hafta=' + hid) : '');
  };
  document.getElementById('btnHisobot').onclick = function(){
    var rows = [];
    HAFTA.forEach(function(h){
      var hamma = [];
      Object.keys(h.items).forEach(function(k){ h.items[k].forEach(function(x,i){ hamma.push([x, i>0]); }); });
      (h.boshqa||[]).forEach(function(x){ hamma.push([x, false]); });
      hamma.forEach(function(pair){
        var x = pair[0];
        if (x.qty>0) rows.push({ ish:x.name+(x.bolim?(' ('+x.bolim+')'):''), hafta:h.raqam, locked:h.locked,
                                 unit:x.unit, vol:x.qty, narx:x.price,
                                 summa:x.sum!==undefined?x.sum:(x.qty*x.price),
                                 izoh: pair[1] ? (x.izoh||'бўлинган — янги нарх') : (x.izoh||'') });
      });
    });
    rows.sort(function(a,b){ return a.ish.localeCompare(b.ish) || a.hafta-b.hafta; });
    var html = '<thead><tr><th class="g-main">Иш тури</th><th class="g-main">Ҳафта</th><th class="g-main">Объём</th><th class="g-main">Ўша пайт нархи</th><th class="g-main">Сумма</th><th class="g-main">Изоҳ</th></tr></thead><tbody>';
    var joriy = null, s = { vol:0, summa:0, unit:'' };
    function jamiSatr(){ return '<tr class="subtotal"><td colspan="2" style="text-align:right;">Жами:</td><td class="num">'+fmt(s.vol)+' '+esc(s.unit)+'</td><td class="num">ўртача '+(s.vol?fmt(s.summa/s.vol):0)+'</td><td class="num">'+fmt(s.summa)+'</td><td></td></tr>'; }
    rows.forEach(function(r){
      if (joriy!==null && joriy!==r.ish){ html += jamiSatr(); s = { vol:0, summa:0, unit:'' }; }
      joriy = r.ish; s.vol+=r.vol; s.summa+=r.summa; s.unit=r.unit;
      html += '<tr'+(r.izoh?' class="split"':'')+'><td>'+esc(r.ish)+'</td><td class="ctr">'+r.hafta+'-ҳафта '+(r.locked?'🔒':'✏️')+'</td><td class="num">'+fmt(r.vol)+' '+esc(r.unit)+'</td><td class="num">'+fmt(r.narx)+'</td><td class="num">'+fmt(r.summa)+'</td><td style="font-size:11.5px;">'+esc(r.izoh||'')+'</td></tr>';
    });
    if (joriy!==null) html += jamiSatr();
    if (!rows.length) html += '<tr><td colspan="6" style="text-align:center; color:#94a3b8; padding:20px;">Ҳали ҳафталик сўровлар йўқ.</td></tr>';
    html += '</tbody>';
    document.getElementById('hisobotTbl').innerHTML = html;
    document.getElementById('jadvalWrap').style.display='none';
    document.getElementById('hisobotWrap').style.display='';
  };
  document.getElementById('btnHisobotYop').onclick = function(){
    document.getElementById('hisobotWrap').style.display='none';
    document.getElementById('jadvalWrap').style.display='';
  };

  // -------- 💾 АВТОСАҚЛАШ: свет ўчса ҳам ёзилганлар йўқолмайди --------
  var AUTO_PREF = 'ljauto_' + C.PK + '_l' + C.LIM_RAQAM + '_';
  function autoKey(){ return AUTO_PREF + sel; }
  function snapshotOl(){
    var rows = {}, lim = {};
    ishRows().forEach(function(tr){
      var iq=tr.querySelector('.in-q'), ip=tr.querySelector('.in-p'), ii=tr.querySelector('.in-i');
      if (iq && ((iq.value||'')!=='' || (ii&&ii.value) ||
                 (ip && (ip.value||'')!=='' && Math.abs(pf(ip.value)-pf(tr.dataset.price))>0.0001)))
        rows[tr.dataset.key] = { q:iq.value, p:ip?ip.value:'', i:ii?ii.value:'' };
    });
    if (LIM_EDIT){
      // faqat o'zgargan mavjud qatorlar va bloklar
      var lp = limPayload();
      lp.edits.forEach(function(e){ lim[e.id] = limRows[e.id]; });
      lp.del.forEach(function(id){ lim[id] = limRows[id]; });
    }
    return { t:Date.now(),
      ws:(document.getElementById('wkStart')||{}).value||'',
      we:(document.getElementById('wkEnd')||{}).value||'',
      rows:rows, lim:lim, limBloklar:(LIM_EDIT ? limBloklar : {}), token:(TAKLIF_EDIT ? TAKLIF_TOKEN : ''),
      bolaklar:bolaklar, extralar:extralar, yangiBloklar:yangiBloklar };
  }
  var autoT = null;
  function autoSaqla(){
    if (!tahrirmi() || yuborilmoqda) return;
    clearTimeout(autoT);
    autoT = setTimeout(function(){
      try {
        if (yuborilmoqda) return;
        var s = snapshotOl();
        var bolakBor = Object.keys(bolaklar).some(function(k){ return (bolaklar[k]||[]).length; });
        var bosh = !Object.keys(s.rows).length && !Object.keys(s.lim).length &&
                   !bolakBor && !extralar.length && !yangiBloklar.length;
        if (bosh) localStorage.removeItem(autoKey());
        else localStorage.setItem(autoKey(), JSON.stringify(s));
      } catch(e){}
    }, 600);
  }
  function autoTikla(){
    if (!tahrirmi()) return;
    var s = null;
    try { s = JSON.parse(localStorage.getItem(autoKey())||'null'); } catch(e){}
    if (!s || !s.rows) return;
    if (!LIM_EDIT && !Object.keys(s.rows).length && !(s.extralar||[]).length &&
        !(TAKLIF_EDIT && s.token === TAKLIF_TOKEN && (s.yangiBloklar||[]).length) &&
        !Object.keys(s.bolaklar||{}).some(function(k){ return (s.bolaklar[k]||[]).length; })){
      // faqat Умумий (blok) tahriri saqlangan edi, u esa endi yopiq — tiklanadigan narsa yo'q
      try { localStorage.removeItem(autoKey()); } catch(e){}
      return;
    }
    // Умумий tahriri yopiq bo'lsa (zanjir/qulf) — eski saqlanmagan bloklar tiklanmaydi
    if (LIM_EDIT){
      Object.keys(s.lim||{}).forEach(function(id){ if (s.lim[id] && typeof s.lim[id] === 'object' && 'name' in s.lim[id]) limRows[id] = s.lim[id]; });
      Object.keys(s.limBloklar||{}).forEach(function(k){ limBloklar[k] = s.limBloklar[k]; });
    }
    // MUHIM: renderAll o'zi extralar/bolaklarni joriy haftadan qayta o'rnatadi,
    // shuning uchun snapshot AVVAL render, KEYIN ustiga qo'yiladi
    renderAll(); requestAnimationFrame(nomKengayHammasi);
    extralar = s.extralar || [];
    if (LIM_EDIT) yangiBloklar = s.yangiBloklar || [];
    else if (TAKLIF_EDIT){ if (s.token === TAKLIF_TOKEN && (s.yangiBloklar||[]).length) yangiBloklar = s.yangiBloklar; }
    else yangiBloklar = [];
    Object.keys(s.bolaklar||{}).forEach(function(k){
      if ((s.bolaklar[k]||[]).length) bolaklar[k] = s.bolaklar[k];
    });
    chizExtralar();
    chizYangiBloklar();
    Object.keys(s.bolaklar||{}).forEach(function(k){
      if ((s.bolaklar[k]||[]).length) redrawKey(k);
    });
    var wsI=document.getElementById('wkStart'), weI=document.getElementById('wkEnd');
    if (wsI && s.ws) wsI.value=s.ws;
    if (weI && s.we) weI.value=s.we;
    Object.keys(s.rows).forEach(function(k){
      var tr=document.querySelector('#ljBody tr.ish[data-key="'+CSS.escape(k)+'"]'); if(!tr) return;
      var v=s.rows[k], iq=tr.querySelector('.in-q'), ip=tr.querySelector('.in-p'), ii=tr.querySelector('.in-i');
      if (iq && v.q!=='') iq.value=v.q;
      if (ip && v.p!=='') ip.value=v.p;
      if (ii && v.i){ ii.value=v.i; izohKengay(ii); wiBelgi(tr.querySelector('.in-i-tgl'), ii); }
    });
    hisobla();
    var el = document.getElementById('ljAuto');
    if (el){
      el.style.display='flex';
      el.querySelector('.t').textContent = new Date(s.t).toLocaleString('ru-RU');
      // Qaysi tugma: Умумий (blok/qator) tiklangan bo'lsa — qoralama/limit, haftalik bo'lsa — Сақлаш
      var limTik = LIM_EDIT && (yangiBloklar.length || Object.keys(s.lim||{}).length);
      var wkTik = Object.keys(s.rows||{}).length || extralar.length;
      var q = el.querySelector('.qaysi');
      if (q){
        var m = [];
        if (limTik) m.push('Умумий лимит учун — «💾 Қоралама сақлаш» (кейин давом этасиз) ёки «▶ Лимитни ' + (ADM_TOGRI ? 'қўллаш' : 'юбориш') + '»');
        if (wkTik) m.push('ҳафталик учун — яшил «💾 Сақлаш»');
        q.textContent = m.length ? ('Ишончли сақлаш учун: ' + m.join('; ') + '.') : 'Ишончли сақлаш учун «Сақлаш»ни босинг.';
      }
    }
  }
  var acBtn = document.getElementById('ljAutoClear');
  if (acBtn) acBtn.onclick = function(){
    if (!confirm('Тикланган (сақланмаган) ўзгаришлар ўчирилади. Давом этамизми?')) return;
    try { localStorage.removeItem(autoKey()); } catch(e){}
    location.reload();
  };
  ['wkStart','wkEnd'].forEach(function(id){
    var el = document.getElementById(id);
    if (el){ el.addEventListener('change', autoSaqla); el.addEventListener('input', wkHeadYangila); el.addEventListener('change', wkHeadYangila); }
  });
  // Hafta BOSHI yozilsa — TUGASHI avtomatik (+6 kun, 7 kunlik hafta)
  (function(){
    var ws0 = document.getElementById('wkStart'), we0 = document.getElementById('wkEnd');
    if (!ws0 || !we0) return;
    ws0.addEventListener('change', function(){
      if (!ws0.value) return;
      var d = new Date(ws0.value + 'T00:00:00');
      if (isNaN(d.getTime())) return;
      d.setDate(d.getDate() + 6);
      var m = String(d.getMonth()+1).padStart(2,'0'), dd = String(d.getDate()).padStart(2,'0');
      we0.value = d.getFullYear() + '-' + m + '-' + dd;
      wkHeadYangila();
      autoSaqla();
    });
    // Umumiy limit davri: boshlanish yozilsa tugash bo'sh bo'lsa hech narsa majburlanmaydi (davr erkin)
  })();
  // Eski (14 kundan o'tgan) avtosaqlashlarni tozalash
  try {
    for (var ai = localStorage.length - 1; ai >= 0; ai--){
      var kk = localStorage.key(ai);
      if (kk && kk.indexOf('ljauto_') === 0){
        var vv = JSON.parse(localStorage.getItem(kk)||'null');
        if (!vv || !vv.t || (Date.now()-vv.t) > 14*24*3600*1000) localStorage.removeItem(kk);
      }
    }
  } catch(e){}

  window.addEventListener('pageshow', function(e){ if (e.persisted) yuborilmoqda = false; });

  renderAll(); requestAnimationFrame(nomKengayHammasi); setTimeout(nomKengayHammasi, 300);
  autoTikla();
})();
