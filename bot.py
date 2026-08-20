<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Qulay</title>
<script src="https://www.gstatic.com/firebasejs/10.13.0/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.13.0/firebase-database-compat.js"></script>
<style>
  :root{
    --bg:#F1F3F9; --card:#FFF; --ink:#131A2E; --soft:#5D6880; --mut:#8C97AE; --line:#E2E7F1;
    --ind:#4B4BE8; --vio:#7C4DF5; --grn:#0E9E6E; --grn2:#12B87F; --amb:#F0921F; --red:#E0483C;
  }
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  html,body{margin:0;background:var(--bg);color:var(--ink);
    font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    -webkit-font-smoothing:antialiased}
  .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
  button{font-family:inherit}
  #app{max-width:440px;margin:0 auto;min-height:100vh;padding-bottom:30px;transition:padding-top .3s}
  body.conned #app{padding-top:30px}
  @media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}

  /* связь */
  .conn{position:fixed;top:0;left:0;right:0;z-index:70;padding:7px 14px;font-size:12px;
    font-weight:700;text-align:center;color:#fff;transform:translateY(-100%);transition:.3s}
  .conn.show{transform:translateY(0)}
  .conn.bad{background:var(--red)}
  .conn.ok{background:var(--grn)}

  header{padding:20px 20px 14px}
  .brand{display:flex;align-items:center;justify-content:space-between}
  .brand .n{font-size:12px;font-weight:800;letter-spacing:.2em;text-transform:uppercase}
  .cl .brand .n{color:var(--ind)} .vo .brand .n{color:var(--grn)}
  .ava{width:31px;height:31px;border-radius:50%;background:var(--ink);color:#fff;font-size:12px;
    font-weight:700;display:flex;align-items:center;justify-content:center}
  .hi{margin:14px 0 0;font-size:26px;font-weight:800;letter-spacing:-.03em;line-height:1.12}
  .hi span{color:var(--mut)}
  main{padding:0 20px}
  .lbl{font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:var(--mut);
    font-weight:800;margin:22px 0 10px}
  .note{font-size:12.5px;color:var(--soft);margin:-3px 0 11px;line-height:1.45}

  .card{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:15px}
  .card+.card{margin-top:9px}

  .order{position:relative;overflow:hidden;border:none;width:100%;text-align:left;cursor:pointer;
    background:linear-gradient(135deg,var(--ind),var(--vio));color:#fff;border-radius:19px;
    padding:19px;box-shadow:0 10px 25px -13px rgba(75,75,232,.8)}
  .order .k{font-size:10.5px;letter-spacing:.13em;text-transform:uppercase;opacity:.86;font-weight:700}
  .order .t{font-size:22px;font-weight:800;letter-spacing:-.025em;margin-top:6px;line-height:1.15}
  .order .s{font-size:13px;opacity:.9;margin-top:5px}
  .order .arr{position:absolute;right:16px;bottom:16px;width:35px;height:35px;border-radius:50%;
    background:rgba(255,255,255,.22);display:flex;align-items:center;justify-content:center;font-size:16px}
  .order .blob{position:absolute;right:-30px;top:-42px;width:140px;height:140px;border-radius:50%;
    background:rgba(255,255,255,.12)}

  .imp{display:flex;gap:8px}
  .imp .b{flex:1;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:11px 10px}
  .imp .n{font-size:20px;font-weight:800;letter-spacing:-.03em}
  .imp .c{font-size:10px;color:var(--mut);margin-top:3px;font-weight:600;line-height:1.3}

  .seg{display:flex;gap:4px;background:#E7EBF4;border-radius:11px;padding:4px;margin-bottom:11px}
  .seg button{flex:1;border:none;background:none;border-radius:8px;padding:10px;font-size:13.5px;
    font-weight:700;color:var(--soft);cursor:pointer}
  .seg button.on{background:var(--card);color:var(--ink);box-shadow:0 1px 4px rgba(19,26,46,.1)}
  .nowcard{border-color:#CFEBDF;background:#F5FCF9}

  .days{display:flex;gap:7px;margin-bottom:10px}
  .day{flex:1;border:1.5px solid var(--line);background:var(--card);border-radius:11px;
    padding:10px 5px;cursor:pointer;display:flex;flex-direction:column;gap:2px}
  .day.on{border-color:var(--ind);background:#F3F3FE}
  .day .dd{font-size:9.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);font-weight:700}
  .day.on .dd{color:var(--ind)}
  .day .dn{font-size:13.5px;font-weight:700}
  .times{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}
  .time{border:1.5px solid var(--line);background:var(--card);border-radius:9px;padding:10px 3px;
    font-size:13.5px;font-weight:700;cursor:pointer;color:var(--ink);
    font-family:ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums}
  .time.on{border-color:var(--ind);background:var(--ind);color:#fff}
  .hint{font-size:12px;color:var(--soft);margin:10px 0 0;line-height:1.45}

  .f{margin-bottom:11px}
  .f label{display:block;font-size:11px;letter-spacing:.09em;text-transform:uppercase;
    color:var(--soft);font-weight:800;margin-bottom:5px}
  .f input,.f textarea{width:100%;border:1.5px solid var(--line);border-radius:10px;padding:12px;
    font-size:16px;font-family:inherit;background:var(--card);color:var(--ink)}
  .f textarea{resize:vertical;min-height:60px}
  input:focus,textarea:focus{outline:2.5px solid var(--ind);outline-offset:0;border-color:transparent}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}

  .live{border-radius:19px;padding:18px;color:#fff}
  .cl .live{background:linear-gradient(135deg,var(--ind),var(--vio));
    box-shadow:0 10px 25px -14px rgba(75,75,232,.75)}
  .vo .live{background:linear-gradient(135deg,var(--grn),var(--grn2));
    box-shadow:0 10px 25px -14px rgba(14,158,110,.75)}
  .live .k{font-size:10.5px;letter-spacing:.13em;text-transform:uppercase;opacity:.88;font-weight:700}
  .live .eta{font-size:34px;font-weight:800;letter-spacing:-.045em;margin-top:4px;line-height:1}
  .live .a{font-size:22px;font-weight:800;letter-spacing:-.03em;margin-top:5px;line-height:1.15}
  .live .s{font-size:13.5px;opacity:.93;margin-top:6px}
  .who{display:flex;gap:11px;align-items:center;margin-top:15px;padding-top:14px;
    border-top:1px solid rgba(255,255,255,.24)}
  .who .ph{width:40px;height:40px;border-radius:50%;background:rgba(255,255,255,.24);
    display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:800;flex-shrink:0}
  .who .b{flex:1;min-width:0}
  .who .nm{font-size:15px;font-weight:700}
  .who .rt{font-size:12px;opacity:.86;margin-top:2px}

  .lvl{margin-top:14px;padding-top:13px;border-top:1px solid rgba(255,255,255,.24)}
  .lvl .row{display:flex;justify-content:space-between;font-size:12px;opacity:.92;font-weight:600}
  .lvlbar{height:6px;border-radius:4px;background:rgba(255,255,255,.28);margin-top:7px;overflow:hidden}
  .lvlbar i{display:block;height:100%;background:#fff;border-radius:4px;transition:width .5s}

  .steps{margin-top:3px}
  .step{display:flex;gap:12px;padding:10px 0}
  .step .col{display:flex;flex-direction:column;align-items:center;flex-shrink:0}
  .step .dot{width:23px;height:23px;border-radius:50%;background:#E7EBF4;color:var(--mut);
    display:flex;align-items:center;justify-content:center;font-size:10.5px;font-weight:800;transition:.25s}
  .cl .step.on .dot{background:var(--ind);color:#fff;box-shadow:0 0 0 5px rgba(75,75,232,.16)}
  .vo .step.on .dot{background:var(--grn);color:#fff;box-shadow:0 0 0 5px rgba(14,158,110,.16)}
  .step.ok .dot{background:var(--grn);color:#fff}
  .step .ln{width:2px;flex:1;background:#E7EBF4;margin:3px 0;min-height:11px}
  .step.ok .ln{background:var(--grn)}
  .step .b{flex:1;min-width:0;padding-bottom:2px}
  .step .t{font-size:14px;font-weight:700;color:var(--mut);transition:.25s}
  .step.on .t,.step.ok .t{color:var(--ink)}
  .step .m{font-size:12px;color:var(--soft);margin-top:2px}

  .job{display:flex;gap:11px;align-items:flex-start}
  .job .pin{width:40px;height:40px;border-radius:11px;background:#FDF0E0;color:var(--amb);
    display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:17px}
  .job .b{flex:1;min-width:0}
  .job .a{font-size:15.5px;font-weight:700}
  .job .m{font-size:12.5px;color:var(--soft);margin-top:3px}
  .tags{display:flex;gap:5px;margin-top:7px;flex-wrap:wrap}
  .tag{font-size:10.5px;font-weight:700;background:#EFF2F8;color:var(--soft);padding:4px 9px;border-radius:20px}
  .tag.hot{background:#FDF0E0;color:#B4620A}

  .btn{border:none;border-radius:12px;padding:14px;font-size:15px;font-weight:800;width:100%;
    cursor:pointer;letter-spacing:-.01em}
  .btn.acc{background:var(--ind);color:#fff}
  .btn.grn{background:var(--grn);color:#fff}
  .btn.dark{background:var(--ink);color:#fff}
  .btn.gh{background:transparent;color:var(--soft);border:1.5px solid var(--line)}
  .btn:disabled{background:#E2E7F1;color:var(--mut);cursor:default}
  .pair{display:flex;gap:7px;margin-top:11px}
  .pair .btn{flex:1;width:auto;min-width:0;padding:12px;font-size:13.5px;border-radius:10px}
  .take{width:100%;margin-top:11px;border:none;background:var(--grn);color:#fff;border-radius:10px;
    padding:12px;font-size:14px;font-weight:800;cursor:pointer}
  .bar{padding:14px 20px 6px}
  .back{border:none;background:none;color:var(--soft);font-size:13.5px;font-weight:700;
    cursor:pointer;padding:5px 0}

  .photo{border:2px dashed var(--line);border-radius:13px;padding:20px 14px;text-align:center;
    background:var(--card);cursor:pointer;margin-top:11px;display:block}
  .photo.done{border-style:solid;border-color:var(--grn);background:#F5FCF9}
  .photo .e{font-size:28px}
  .photo .t{font-size:14px;font-weight:700;margin-top:7px}
  .photo .m{font-size:12px;color:var(--soft);margin-top:3px}
  .photo img{max-width:100%;border-radius:9px;margin-top:9px}
  .photo input{display:none}

  .fin{text-align:center;padding:24px 0 6px}
  .fin .em{font-size:52px;line-height:1;animation:pop .5s cubic-bezier(.2,1.3,.4,1)}
  @keyframes pop{0%{transform:scale(.3);opacity:0}100%{transform:scale(1);opacity:1}}
  .fin h2{margin:13px 0 0;font-size:23px;font-weight:800;letter-spacing:-.03em}
  .fin p{margin:8px auto 0;font-size:14px;color:var(--soft);max-width:30ch;line-height:1.5}
  .stars{display:flex;justify-content:center;gap:6px;margin-top:18px}
  .stars button{border:none;background:none;font-size:29px;cursor:pointer;opacity:.28;
    filter:grayscale(1);transition:.14s;padding:2px}
  .stars button.on{opacity:1;filter:none;transform:scale(1.1)}

  .empty{border:1px dashed var(--line);border-radius:13px;padding:26px 16px;text-align:center;
    color:var(--soft);font-size:13.5px;background:var(--card)}
  .empty .e{font-size:30px;opacity:.5}
  .empty .t{font-size:15px;font-weight:700;color:var(--ink);margin-top:10px}
  .empty .m{margin-top:6px;line-height:1.5}

  .pulse{display:inline-block;width:6px;height:6px;border-radius:50%;background:#fff;
    margin-right:6px;animation:pl 1.9s infinite}
  @keyframes pl{0%,100%{opacity:1}50%{opacity:.3}}

  /* ---------- УВЕДОМЛЕНИЕ ВОЛОНТЁРУ ---------- */
  .alert{position:fixed;top:0;left:0;right:0;z-index:90;padding:14px 16px;
    background:linear-gradient(135deg,var(--amb),#E0662A);color:#fff;
    box-shadow:0 10px 30px -8px rgba(224,102,42,.7);
    transform:translateY(-130%);transition:transform .35s cubic-bezier(.2,1.1,.3,1)}
  .alert.show{transform:translateY(0);animation:shake .5s}
  @keyframes shake{0%,100%{transform:translateY(0)}
    25%{transform:translateY(0) translateX(-4px)}75%{transform:translateY(0) translateX(4px)}}
  .alert .in{max-width:440px;margin:0 auto;display:flex;gap:12px;align-items:center}
  .alert .bell{width:42px;height:42px;border-radius:12px;background:rgba(255,255,255,.24);
    display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0;
    animation:ring 1s infinite}
  @keyframes ring{0%,100%{transform:rotate(0)}20%{transform:rotate(-14deg)}40%{transform:rotate(14deg)}
    60%{transform:rotate(-8deg)}80%{transform:rotate(8deg)}}
  .alert .b{flex:1;min-width:0}
  .alert .k{font-size:10.5px;letter-spacing:.13em;text-transform:uppercase;opacity:.9;font-weight:800}
  .alert .t{font-size:16px;font-weight:800;margin-top:3px;letter-spacing:-.01em}
  .alert .m{font-size:12.5px;opacity:.92;margin-top:2px}
  .alert .go{border:none;background:#fff;color:#B4520A;border-radius:9px;padding:10px 14px;
    font-size:13.5px;font-weight:800;cursor:pointer;flex-shrink:0}

  .pill{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);z-index:80;
    background:var(--amb);color:#fff;border:none;border-radius:24px;padding:13px 20px;
    font-size:14.5px;font-weight:800;cursor:pointer;box-shadow:0 10px 26px -8px rgba(224,102,42,.75);
    display:none;align-items:center;gap:8px}
  .pill.show{display:flex}

  .onair{display:flex;gap:11px;align-items:center;background:var(--card);border:1.5px solid var(--line);
    border-radius:14px;padding:14px;margin-bottom:12px}
  .onair.on{border-color:var(--grn);background:#F5FCF9}
  .onair .b{flex:1;min-width:0}
  .onair .t{font-size:15px;font-weight:700}
  .onair .m{font-size:12.5px;color:var(--soft);margin-top:2px}
  .sw{width:50px;height:29px;border-radius:16px;background:#D7DDE9;border:none;position:relative;
    cursor:pointer;flex-shrink:0;transition:.2s}
  .sw.on{background:var(--grn)}
  .sw i{position:absolute;top:3px;left:3px;width:23px;height:23px;border-radius:50%;background:#fff;
    transition:.2s;box-shadow:0 1px 3px rgba(0,0,0,.25)}
  .sw.on i{left:24px}

  .toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%) translateY(14px);
    background:var(--ink);color:#fff;padding:12px 18px;border-radius:11px;font-size:14px;
    font-weight:700;opacity:0;pointer-events:none;transition:.22s;z-index:95;
    box-shadow:0 8px 24px -8px rgba(19,26,46,.5);max-width:88%;text-align:center}
  .toast.on{opacity:1;transform:translateX(-50%) translateY(0)}
  .hide{display:none!important}
</style>
</head>
<body>
<div class="conn" id="conn"></div>
<div class="alert" id="alert"></div>
<button class="pill" id="pill" onclick="openAlert()"></button>
<div id="app"></div>
<div class="toast" id="toast"></div>

<script>
/* ==================================================================
   QULAY — рабочая версия. Данные живут в Firebase, поэтому заявка
   с телефона жильца мгновенно появляется у волонтёра.
   Роль задаётся ссылкой:  ?role=client   или   ?role=volunteer
   ================================================================== */
const CFG={
  apiKey:"AIzaSyDgaPirVFDVLUCThA8Nrmqm-JYFQmGTWSQ",
  authDomain:"qulay-bfc96.firebaseapp.com",
  databaseURL:"https://qulay-bfc96-default-rtdb.europe-west1.firebasedatabase.app",
  projectId:"qulay-bfc96",
  storageBucket:"qulay-bfc96.firebasestorage.app",
  messagingSenderId:"889805486547",
  appId:"1:889805486547:web:6197c53b0cc8f2dd441726"
};

const P=new URLSearchParams(location.search);
const ROLE=(P.get('role')==='volunteer')?'vo':'cl';

let uid=localStorage.getItem('qulay_uid');
if(!uid){uid='u'+Math.random().toString(36).slice(2,10);localStorage.setItem('qulay_uid',uid);}

/* профиль: имя + телефон, сохраняется на устройстве и в базе */
let me=null;
try{ me=JSON.parse(localStorage.getItem('qulay_profile')||'null'); }catch(e){ me=null; }
let MYNAME = me?me.name:'Гость';
let regName='', regPhone='';

function phoneDigits(v){let d=(v||'').replace(/\D/g,'');
  if(d.startsWith('998'))d=d.slice(3);return d.slice(0,9);}
function fmtPhone(v){
  const d=phoneDigits(v); let o='+998';
  if(d.length)   o+=' '+d.slice(0,2);
  if(d.length>2) o+=' '+d.slice(2,5);
  if(d.length>5) o+=' '+d.slice(5,7);
  if(d.length>7) o+=' '+d.slice(7,9);
  return o;
}
function telHref(p){return 'tel:+998'+phoneDigits(p);}

let db=null, connOK=false;
let view='home', mode='now', dayI=0, timeV='14:00', bags='Один пакет', rating=0;
let addr=JSON.parse(localStorage.getItem('qulay_addr')||'null')||{h:'',e:'',f:'',q:'',note:''};
let orders={}, myOrder=null, activeJob=null, onair=true, photoURL=null;
let geoCoords=null, geoStatus='idle'; /* idle | asking | ok | denied | unsupported */
let seenIds=new Set(), alertOrder=null, audioCtx=null, alertTimer=null;

const days=[{d:'сегодня'},{d:'завтра'},{d:'послезавтра'}];
const CSTEP=[['Заявка принята','Ищем волонтёра рядом с вами'],
             ['Волонтёр в пути','Идёт к вашему подъезду'],
             ['Волонтёр у двери','Вынесите пакет к двери'],
             ['Мусор доставлен','Донесён до контейнера']];
const VSTEP=[['Идёте к дому','Жилец видит, что вы в пути'],
             ['У подъезда','Жилец выносит пакет'],
             ['Пакет у вас','Донесите до контейнера'],
             ['Готово','Заявка закрыта']];
const ST=['open','taken','arrived','picked','done'];
const phaseOf=o=>o?Math.max(0,ST.indexOf(o.status)):-1;

const $=s=>document.querySelector(s);
const A=o=>`Дом ${o.house}, кв. ${o.flat}`;
const M=o=>`Подъезд ${o.entrance||'—'}, этаж ${o.floor||'—'}`;
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('on');
  clearTimeout(t._x);t._x=setTimeout(()=>t.classList.remove('on'),2600);}
function conn(ok,msg){const c=$('#conn');connOK=ok;
  c.className='conn show '+(ok?'ok':'bad');c.textContent=msg;
  document.body.classList.add('conned');
  if(ok) setTimeout(()=>{c.classList.remove('show');
    document.body.classList.remove('conned');},2200);}
function times(i){const a=[];const st=i===0?Math.min(21,new Date().getHours()+1):8;
  for(let h=st;h<=21;h++){a.push(String(h).padStart(2,'0')+':00');
    if(h<21)a.push(String(h).padStart(2,'0')+':30');}
  return a.length?a:['08:00'];}
function whenLabel(){return mode==='now'?'сейчас':`${days[dayI].d} в ${timeV}`;}

/* ================= геопозиция ================= */
function requestGeo(){
  if(!navigator.geolocation){ geoStatus='unsupported'; render(); return; }
  geoStatus='asking'; render();
  navigator.geolocation.getCurrentPosition(
    pos=>{ geoCoords={lat:pos.coords.latitude,lng:pos.coords.longitude}; geoStatus='ok'; render(); },
    ()=>{ geoStatus='denied'; render(); },
    {enableHighAccuracy:true, timeout:8000, maximumAge:60000}
  );
}
function geoCard(){
  if(geoStatus==='ok'){
    return `<div class="card" style="border-color:#CFEBDF;background:#F5FCF9"><div class="job">
      <div class="pin" style="background:#E4F6EE;color:var(--grn)">📍</div>
      <div class="b"><div class="a">Геопозиция найдена</div>
      <div class="m">Волонтёр увидит точку на карте и построит маршрут прямо к вам</div></div></div></div>`;
  }
  if(geoStatus==='asking'){
    return `<div class="card"><div class="job">
      <div class="pin">📍</div>
      <div class="b"><div class="a">Определяем местоположение…</div>
      <div class="m">Разрешите доступ, если браузер спросит</div></div></div></div>`;
  }
  if(geoStatus==='denied'){
    return `<div class="card" style="border-color:#F6D9C9;background:#FFFBF4"><div class="job">
      <div class="pin" style="background:#FDF0E0;color:var(--amb)">⚠️</div>
      <div class="b"><div class="a">Нет доступа к геопозиции</div>
      <div class="m">Без неё волонтёру будет сложнее вас найти. Заявку всё равно можно отправить</div>
      </div></div><button class="take" onclick="requestGeo()">Разрешить ещё раз</button></div>`;
  }
  if(geoStatus==='unsupported'){
    return `<div class="card"><div class="job"><div class="pin">⚠️</div>
      <div class="b"><div class="a">Браузер не поддерживает геопозицию</div>
      <div class="m">Заявка всё равно будет принята</div></div></div></div>`;
  }
  return `<div class="card"><div class="job"><div class="pin">📍</div>
    <div class="b"><div class="a">Определить местоположение</div>
    <div class="m">Поможет волонтёру найти вас быстрее</div></div></div>
    <button class="take" onclick="requestGeo()">Определить</button></div>`;
}

/* ================= подключение ================= */
function boot(){
  document.getElementById('app').className=ROLE;
  try{
    firebase.initializeApp(CFG);
    db=firebase.database();
    db.ref('.info/connected').on('value',s=>{
      if(s.val()===true){ if(!connOK) conn(true,'Связь есть — заявки приходят мгновенно'); }
      else conn(false,'Нет связи с сервером. Проверьте интернет');
    });
    listen();
  }catch(e){ conn(false,'Не удалось подключиться к серверу'); }
  render();
}

function listen(){
  db.ref('orders').limitToLast(40).on('value',snap=>{
    orders=snap.val()||{};
    if(ROLE==='cl'){
      const mine=Object.entries(orders).filter(([,o])=>o.clientId===uid)
        .sort((a,b)=>b[1].createdAt-a[1].createdAt);
      const live=mine.find(([,o])=>o.status!=='done'&&o.status!=='cancelled');
      myOrder=live?{id:live[0],...live[1]}:null;
      if(myOrder && view==='new') view='live';
      if(!myOrder && view==='live') view='home';
    }else{
      const act=Object.entries(orders).find(([,o])=>o.volunteerId===uid&&o.status!=='done');
      activeJob=act?{id:act[0],...act[1]}:null;
      checkNew();
    }
    render();
  },()=>conn(false,'Нет доступа к базе. Проверьте правила Firebase'));
}

/* ================= уведомление волонтёру ================= */
function openOrders(){
  return Object.entries(orders).filter(([,o])=>o.status==='open')
    .sort((a,b)=>b[1].createdAt-a[1].createdAt).map(([id,o])=>({id,...o}));
}
function checkNew(){
  if(!onair||activeJob) return;
  const list=openOrders();
  const fresh=list.filter(o=>!seenIds.has(o.id));
  list.forEach(o=>seenIds.add(o.id));
  if(fresh.length){ fireAlert(fresh[0]); }
  const p=$('#pill');
  if(list.length&&view==='home'&&!alertOrder){p.classList.remove('show');}
}
function fireAlert(o){
  alertOrder=o;
  const a=$('#alert');
  a.innerHTML=`<div class="in"><div class="bell">🔔</div>
    <div class="b"><div class="k">Новая заявка рядом</div>
      <div class="t">${A(o)}</div>
      <div class="m">${M(o)} · ${o.bags||''}</div></div>
    <button class="go" onclick="openAlert()">Открыть</button></div>`;
  a.classList.add('show');
  beep(); buzz(); flashTitle();
  notify(o);
  clearTimeout(alertTimer);
  alertTimer=setTimeout(()=>{a.classList.remove('show');showPill();},15000);
}
function showPill(){
  const n=openOrders().length;
  const p=$('#pill');
  if(n&&!activeJob){p.innerHTML=`🔔 ${n} ${n===1?'новая заявка':'новых заявок'}`;p.classList.add('show');}
  else p.classList.remove('show');
}
function openAlert(){
  clearTimeout(alertTimer);
  $('#alert').classList.remove('show');$('#pill').classList.remove('show');
  const o=alertOrder||openOrders()[0];
  alertOrder=null;
  if(o){ view='job'; activeJob={...o}; }
  render();
}
function beep(){
  try{
    audioCtx=audioCtx||new (window.AudioContext||window.webkitAudioContext)();
    if(audioCtx.state==='suspended') audioCtx.resume();
    [0,220,440].forEach(d=>setTimeout(()=>{
      const o=audioCtx.createOscillator(),g=audioCtx.createGain();
      o.type='sine';o.frequency.value=920;
      g.gain.setValueAtTime(.0001,audioCtx.currentTime);
      g.gain.exponentialRampToValueAtTime(.35,audioCtx.currentTime+.02);
      g.gain.exponentialRampToValueAtTime(.0001,audioCtx.currentTime+.22);
      o.connect(g);g.connect(audioCtx.destination);o.start();o.stop(audioCtx.currentTime+.24);
    },d));
  }catch(e){}
}
function buzz(){ if(navigator.vibrate) navigator.vibrate([220,110,220,110,320]); }
function flashTitle(){
  let i=0; const t0=document.title;
  const iv=setInterval(()=>{document.title=(i++%2)?'🔔 НОВАЯ ЗАЯВКА':'Qulay';
    if(i>10){clearInterval(iv);document.title=t0;}},650);
}
function notify(o){
  try{
    if(!('Notification' in window)) return;
    if(Notification.permission==='granted')
      new Notification('Новая заявка рядом',{body:A(o)+' · '+M(o),tag:o.id});
  }catch(e){}
}
function askNotify(){
  try{ if('Notification' in window && Notification.permission==='default')
    Notification.requestPermission(); }catch(e){}
}

/* ================= регистрация ================= */
function regView(){
  const ok=phoneDigits(regPhone).length===9 && regName.trim().length>=2;
  const isVo=ROLE==='vo';
  return `<header><div class="brand"><div class="n">Qulay${isVo?' · волонтёр':''}</div></div>
      <h1 class="hi">${isVo?'Регистрация волонтёра':'Добро пожаловать'}<br>
      <span>${isVo?'Жильцы будут видеть ваше имя':'Займёт полминуты'}</span></h1></header>
    <main>
      <div class="card" style="border-color:${isVo?'#CFEBDF':'#DCDCFB'};
        background:${isVo?'#F5FCF9':'#F7F7FE'}">
        <div class="job"><div class="pin" style="background:${isVo?'#E4F6EE':'#EEF0FE'};
          color:${isVo?'var(--grn)':'var(--ind)'}">📞</div>
        <div class="b"><div class="a">Зачем номер</div>
        <div class="m">${isVo
          ? 'Жилец сможет позвонить, если не может выйти или нужно уточнить подъезд.'
          : 'Волонтёр позвонит, если не найдёт подъезд или домофон не работает. Больше номер никто не увидит.'}</div>
        </div></div></div>

      <div class="lbl">Ваши данные</div>
      <div class="f"><label>Как вас зовут</label>
        <input id="r_name" value="${regName}" placeholder="${isVo?'Элёр':'Дилноза'}"
          oninput="regName=this.value;liveCheck()"></div>
      <div class="f"><label>Номер телефона</label>
        <input id="r_phone" type="tel" inputmode="tel" value="${regPhone||'+998 '}"
          oninput="onPhone(this)" placeholder="+998 90 123 45 67"></div>
      <p class="hint">Номер сохраняется только для связи по заявке.</p>

      <div class="bar"><button class="btn ${isVo?'grn':'acc'}" id="r_btn" ${ok?'':'disabled'}
        onclick="saveProfile()">Продолжить</button></div>
    </main>`;
}
function onPhone(inp){
  const pos=inp.selectionStart, before=inp.value.length;
  regPhone=fmtPhone(inp.value);
  inp.value=regPhone;
  const d=Math.max(0,inp.value.length-before);
  try{inp.setSelectionRange(pos+d,pos+d);}catch(e){}
  liveCheck();
}
function liveCheck(){
  const b=$('#r_btn'); if(!b) return;
  b.disabled=!(phoneDigits(regPhone).length===9 && regName.trim().length>=2);
}
function saveProfile(){
  const name=regName.trim(), phone=fmtPhone(regPhone);
  if(name.length<2||phoneDigits(phone).length!==9){toast('Проверьте имя и номер');return;}
  me={name,phone,role:ROLE,createdAt:Date.now()};
  MYNAME=name;
  localStorage.setItem('qulay_profile',JSON.stringify(me));
  if(db) db.ref('users/'+uid).update(me).catch(()=>{});
  view='home';render();
  toast('Готово, '+name+'!');
}
function editProfile(){
  regName=me?me.name:''; regPhone=me?me.phone:'';
  view='reg';render();
}

/* ================= рендер ================= */
function render(){
  if(!me||!me.phone){ $('#app').innerHTML=regView(); return; }
  const v = ROLE==='cl'
    ? ({home:cHome,new:cNew,live:cLive,fin:cFin,reg:regView}[view]||cHome)
    : ({home:vHome,job:vJob,work:vWork,fin:vFin,reg:regView}[view]||vHome);
  $('#app').innerHTML=v();
  if(ROLE==='vo'&&view==='home') showPill();
}

/* ---------------- КЛИЕНТ ---------------- */
function cHome(){
  const done=Object.values(orders).filter(o=>o.clientId===uid&&o.status==='done');
  return `<header><div class="brand"><div class="n">Qulay</div>
      <div class="ava">${MYNAME[0]}</div></div>
      <h1 class="hi">Привет, ${MYNAME}<br><span>Вынести мусор — одна кнопка</span></h1></header>
    <main>
      <div class="prof"><div class="b"><div class="nm">${me.name}</div>
        <div class="ph">${me.phone}</div></div>
        <button onclick="editProfile()">Изменить</button></div>
      <button class="order" onclick="go('new')"><div class="blob"></div>
        <div class="k">Волонтёры на связи</div>
        <div class="t">Забрать мусор<br>из квартиры</div>
        <div class="s">${addr.h?`Дом ${addr.h}, кв. ${addr.q} · `:''}сейчас или ко времени</div>
        <div class="arr">→</div></button>
      <div class="lbl">Ваш вклад</div>
      <div class="imp">
        <div class="b"><div class="n mono">${done.length}</div><div class="c">раз вынесли<br>за вас</div></div>
        <div class="b"><div class="n mono">${done.length*3}<small style="font-size:12px"> кг</small></div>
          <div class="c">не осталось<br>у подъезда</div></div>
        <div class="b"><div class="n mono">#1</div><div class="c">место<br>в вашем доме</div></div></div>
      <div class="lbl">История</div>
      ${done.length? done.slice(0,5).map(o=>`<div class="card"><div class="job">
          <div class="pin" style="background:#E4F6EE;color:var(--grn)">✓</div>
          <div class="b"><div class="a">${A(o)}</div>
          <div class="m">${new Date(o.createdAt).toLocaleString('ru',{day:'numeric',month:'long',
            hour:'2-digit',minute:'2-digit'})} · забрал ${o.volunteerName||'волонтёр'}</div>
          <div class="tags"><span class="tag">${o.bags||''}</span></div></div></div></div>`).join('')
        : `<div class="empty"><div class="e">📦</div><div class="t">Заявок пока не было</div>
           <div class="m">Первую можно оставить прямо сейчас — волонтёр придёт за 10–20 минут.</div></div>`}
    </main>`;
}

function cNew(){
  return `<header><div class="brand"><div class="n">Qulay</div>
      <div class="ava">${MYNAME[0]}</div></div><h1 class="hi">Новая заявка</h1></header>
    <main><button class="back" onclick="go('home')">← Назад</button>
      <div class="lbl">Когда забрать</div>
      <div class="seg"><button class="${mode==='now'?'on':''}" onclick="setMode('now')">Сейчас</button>
        <button class="${mode==='time'?'on':''}" onclick="setMode('time')">Ко времени</button></div>
      ${mode==='now'
        ? `<div class="card nowcard"><div class="job">
            <div class="pin" style="background:#E4F6EE;color:var(--grn)">⚡</div>
            <div class="b"><div class="a">Ближайший волонтёр</div>
            <div class="m">Придёт за 10–20 минут</div></div></div></div>`
        : `<div class="days">${days.map((d,i)=>`<button class="day ${dayI===i?'on':''}"
             onclick="pickDay(${i})"><span class="dd">${d.d}</span></button>`).join('')}</div>
           <div class="times">${times(dayI).map(t=>`<button class="time ${timeV===t?'on':''}"
             onclick="pickTime('${t}')">${t}</button>`).join('')}</div>
           <p class="hint">Волонтёры работают с 08:00 до 21:00 — выбирайте любое удобное время.</p>`}
      <div class="lbl">Адрес</div>
      <div class="grid">
        <div class="f"><label>Дом</label><input id="i_h" value="${addr.h}" placeholder="12"></div>
        <div class="f"><label>Подъезд</label><input id="i_e" value="${addr.e}" placeholder="2"></div></div>
      <div class="grid">
        <div class="f"><label>Этаж</label><input id="i_f" value="${addr.f}" placeholder="5"></div>
        <div class="f"><label>Квартира</label><input id="i_q" value="${addr.q}" placeholder="34"></div></div>
      <div class="f"><label>Комментарий</label>
        <textarea id="i_n" placeholder="Код домофона, лифт не работает">${addr.note||''}</textarea></div>
      <div class="lbl">Геопозиция</div>
      ${geoCard()}
      <div class="lbl">Что выносим</div>
      <div class="tags">${['Один пакет','Два-три пакета','Крупный мусор','Стекло / банки']
        .map(c=>`<button class="tag" style="cursor:pointer;font-size:12.5px;padding:9px 13px;
          border:1.5px solid ${bags===c?'var(--ind)':'var(--line)'};background:${
          bags===c?'#F3F3FE':'var(--card)'};color:${bags===c?'var(--ind)':'var(--soft)'}"
          onclick="pickBags('${c}')">${c}</button>`).join('')}</div>
      <div class="bar"><button class="btn acc" onclick="submit()">${
        mode==='now'?'Вызвать волонтёра сейчас':'Записать на '+whenLabel()}</button></div>
    </main>`;
}

function cLive(){
  const o=myOrder; if(!o) return cHome();
  const ph=phaseOf(o), st=Math.min(ph,3);
  return `<header><div class="brand"><div class="n">Qulay</div>
      <div class="ava">${MYNAME[0]}</div></div><h1 class="hi">Заявка в работе</h1></header>
    <main><div class="live">
        <div class="k">${ph===0?'<span class="pulse"></span>Ищем волонтёра':CSTEP[st][0]}</div>
        <div class="a">${A(o)}</div>
        <div class="s">${CSTEP[st][1]}</div>
        ${o.volunteerName?`<div class="who"><div class="ph">${o.volunteerName[0]}</div>
          <div class="b"><div class="nm">${o.volunteerName}</div>
          <div class="rt">${o.volunteerPhone||'волонтёр Qulay'}</div></div>
          ${o.volunteerPhone?`<a class="call" href="${telHref(o.volunteerPhone)}">📞</a>`:''}</div>`:''}
      </div>
      <div class="lbl">Что происходит</div>
      <div class="steps">${CSTEP.map((s,i)=>{
        const cls=i<ph?'ok':i===ph?'on':'';
        return `<div class="step ${cls}"><div class="col"><div class="dot">${i<ph?'✓':i+1}</div>
          ${i<3?'<div class="ln"></div>':''}</div>
          <div class="b"><div class="t">${s[0]}</div><div class="m">${s[1]}</div></div></div>`;}).join('')}</div>
      <div class="bar">${ph===0?`<button class="btn gh" onclick="cancelOrder()">Отменить заявку</button>`:''}</div>
    </main>`;
}

function cFin(){
  return `<main><div class="fin"><div class="em">🎉</div>
      <h2>Мусор забран</h2><p>Волонтёр донёс пакет до контейнера. Вам не пришлось выходить.</p>
      <div class="lbl" style="text-align:center">Как справился волонтёр</div>
      <div class="stars">${[1,2,3,4,5].map(i=>`<button class="${rating>=i?'on':''}"
        onclick="rate(${i})">⭐</button>`).join('')}</div></div>
      <div class="bar"><button class="btn dark" onclick="go('home')">${
        rating?'Отправить и закрыть':'Готово'}</button></div></main>`;
}

/* ---------------- ВОЛОНТЁР ---------------- */
function vHome(){
  const list=openOrders();
  const done=Object.values(orders).filter(o=>o.volunteerId===uid&&o.status==='done');
  return `<header><div class="brand"><div class="n">Qulay · волонтёр</div>
      <div class="ava">${MYNAME[0]}</div></div>
      <h1 class="hi">Привет, ${MYNAME}<br><span>${
        list.length?`${list.length} ${list.length===1?'заявка ждёт':'заявки ждут'}`
        :'заявок рядом пока нет'}</span></h1></header>
    <main>
      <div class="prof"><div class="b"><div class="nm">${me.name}</div>
        <div class="ph">${me.phone}</div></div>
        <button onclick="editProfile()">Изменить</button></div>
      <div class="onair ${onair?'on':''}">
        <div class="b"><div class="t">${onair?'Вы на связи':'Уведомления выключены'}</div>
          <div class="m">${onair?'Пришлём звук и вибрацию, когда появится заявка'
            :'Включите, чтобы не пропустить заявку'}</div></div>
        <button class="sw ${onair?'on':''}" onclick="toggleAir()"><i></i></button></div>
      ${activeJob?`<div class="live" style="margin-bottom:12px">
          <div class="k">Активная заявка</div><div class="a">${A(activeJob)}</div>
          <div class="s">${VSTEP[Math.max(0,phaseOf(activeJob)-1)][0]}</div></div>
        <button class="btn grn" onclick="go('work')">Вернуться к заявке</button>`:''}
      <div class="lbl">Ваши результаты</div>
      <div class="imp">
        <div class="b"><div class="n mono">${done.length}</div><div class="c">заявок<br>закрыто</div></div>
        <div class="b"><div class="n mono">${done.length*20}</div><div class="c">очков<br>набрано</div></div>
        <div class="b"><div class="n mono">#1</div><div class="c">место<br>в районе</div></div></div>
      <div class="lbl">Заявки рядом</div>
      ${list.length? list.map(o=>`<div class="card"><div class="job">
          <div class="pin">📍</div>
          <div class="b"><div class="a">${A(o)}</div><div class="m">${M(o)}</div>
          <div class="tags"><span class="tag">${o.bags||''}</span>
            <span class="tag hot">${ago(o.createdAt)}</span></div>
          ${o.note?`<div class="m" style="margin-top:6px">💬 ${o.note}</div>`:''}</div></div>
          <button class="take" onclick="openJob('${o.id}')">Посмотреть и взять</button></div>`).join('')
        : `<div class="empty"><div class="e">🌿</div><div class="t">Сейчас всё чисто</div>
           <div class="m">Как только жилец оставит заявку, она появится здесь — со звуком.</div></div>`}
    </main>`;
}

function vJob(){
  const o=activeJob; if(!o) {view='home';return vHome();}
  return `<header><div class="brand"><div class="n">Qulay · волонтёр</div>
      <div class="ava">${MYNAME[0]}</div></div><h1 class="hi">Заявка</h1></header>
    <main><button class="back" onclick="go('home')">← Все заявки</button>
      <div class="card"><div class="job"><div class="pin">📍</div>
        <div class="b"><div class="a">${A(o)}</div><div class="m">${M(o)}</div>
        <div class="tags"><span class="tag">${o.bags||''}</span>
          <span class="tag hot">${ago(o.createdAt)}</span></div>
        ${o.note?`<div class="m" style="margin-top:8px">💬 ${o.note}</div>`:''}</div></div></div>
      <div class="lbl">Кто оставил</div>
      <div class="card"><div class="job">
        <div class="pin" style="background:#EEF0FE;color:var(--ind)">${(o.clientName||'Ж')[0]}</div>
        <div class="b"><div class="a">${o.clientName||'Жилец'}</div>
        <div class="m">${o.when==='now'?'просит забрать сейчас':'ко времени: '+(o.when||'')}</div>
        <div class="m" style="margin-top:6px">📞 Номер откроется, когда возьмёте заявку</div>
        </div></div></div>
      <div class="bar"><button class="btn grn" onclick="claim('${o.id}')">Взять заявку</button></div>
    </main>`;
}

function vWork(){
  const o=activeJob; if(!o){view='home';return vHome();}
  const ph=phaseOf(o), st=Math.max(0,Math.min(ph-1,3));
  return `<header><div class="brand"><div class="n">Qulay · волонтёр</div>
      <div class="ava">${MYNAME[0]}</div></div><h1 class="hi">Вы в пути</h1></header>
    <main><div class="live">
        <div class="k">${ph===1?'<span class="pulse"></span>Идёте к дому':VSTEP[st][0]}</div>
        <div class="a">${A(o)}</div><div class="s">${VSTEP[st][1]}</div>
        <div class="who"><div class="ph">${(o.clientName||'Ж')[0]}</div>
          <div class="b"><div class="nm">${o.clientName||'Жилец'}</div>
          <div class="rt">${o.clientPhone||M(o)}</div></div>
          ${o.clientPhone?`<a class="call" href="${telHref(o.clientPhone)}">📞</a>`:''}</div></div>
      ${o.note?`<div class="card" style="margin-top:11px"><div class="m">💬 ${o.note}</div></div>`:''}
      <div class="lbl">Ваши шаги</div>
      <div class="steps">${VSTEP.map((s,i)=>{
        const cls=i<st?'ok':i===st?'on':'';
        return `<div class="step ${cls}"><div class="col"><div class="dot">${i<st?'✓':i+1}</div>
          ${i<3?'<div class="ln"></div>':''}</div>
          <div class="b"><div class="t">${s[0]}</div><div class="m">${s[1]}</div></div></div>`;}).join('')}</div>
      ${ph===3?`<label class="photo ${photoURL?'done':''}">
        <input type="file" accept="image/*" capture="environment" onchange="snap(this)">
        <div class="e">${photoURL?'✅':'📷'}</div>
        <div class="t">${photoURL?'Фото прикреплено':'Фото у контейнера'}</div>
        <div class="m">${photoURL?'Можно закрывать заявку':'Подтверждает, что мусор доехал'}</div>
        ${photoURL?`<img src="${photoURL}" alt="фото">`:''}</label>`:''}
      <div class="bar">${
        ph===1?`<div class="pair">
          <button class="btn gh" onclick="maps()">Маршрут</button>
          <button class="btn grn" onclick="setStatus('arrived')">Я на месте</button></div>`
        :ph===2?`<button class="btn grn" onclick="setStatus('picked')">Пакет забрал</button>`
        :`<button class="btn grn" ${photoURL?'':'disabled'} onclick="setStatus('done')">${
          photoURL?'Закрыть заявку':'Сначала фото'}</button>`}</div>
    </main>`;
}

function vFin(){
  return `<main><div class="fin"><div class="em">🌱</div>
      <h2>Заявка закрыта</h2><p>Пакет доехал до контейнера, а не остался у подъезда.</p></div>
      <div class="bar"><button class="btn dark" onclick="go('home')">Вернуться к заявкам</button></div></main>`;
}

function ago(ts){
  const m=Math.floor((Date.now()-ts)/60000);
  if(m<1) return 'только что';
  if(m<60) return `ждёт ${m} мин`;
  return `ждёт ${Math.floor(m/60)} ч`;
}

/* ================= действия ================= */
function go(v){view=v;rating=0;render();window.scrollTo({top:0,behavior:'smooth'});
  if(v==='new'&&geoStatus==='idle') requestGeo();}
function setMode(m){mode=m;render();}
function pickDay(i){dayI=i;const t=times(i);if(!t.includes(timeV))timeV=t[0];render();}
function pickTime(t){timeV=t;render();}
function pickBags(b){bags=b;render();}
function rate(n){rating=n;render();}
function maps(){const o=activeJob;
  window.open('https://yandex.uz/maps/?text='+encodeURIComponent(A(o)),'_blank');}

function submit(){
  const h=$('#i_h').value.trim(), q=$('#i_q').value.trim();
  if(!h||!q){toast('Укажите дом и квартиру');return;}
  addr={h,e:$('#i_e').value.trim(),f:$('#i_f').value.trim(),q,note:$('#i_n').value.trim()};
  localStorage.setItem('qulay_addr',JSON.stringify(addr));
  if(!db){toast('Нет связи с сервером');return;}
  const ref=db.ref('orders').push();
  ref.set({clientId:uid,clientName:MYNAME,clientPhone:me.phone,
    house:addr.h,entrance:addr.e,floor:addr.f,flat:addr.q,
    note:addr.note,bags,when:mode==='now'?'now':whenLabel(),status:'open',createdAt:Date.now(),
    lat:geoCoords?geoCoords.lat:null,lng:geoCoords?geoCoords.lng:null})
    .then(()=>{view='live';
      geoCoords=null;geoStatus='idle';
      render();
      toast('Заявка отправлена волонтёрам');})
    .catch(()=>toast('Не удалось отправить. Проверьте правила Firebase'));
}
function cancelOrder(){
  if(!myOrder) return;
  db.ref('orders/'+myOrder.id).update({status:'cancelled'})
    .then(()=>{myOrder=null;view='home';render();toast('Заявка отменена');});
}
function openJob(id){activeJob={id,...orders[id]};view='job';render();}
function claim(id){
  db.ref('orders/'+id).transaction(o=>{
    if(!o) return o;
    if(o.status!=='open') return;           // кто-то успел раньше
    o.status='taken'; o.volunteerId=uid; o.volunteerName=MYNAME;
    o.volunteerPhone=me.phone; o.takenAt=Date.now();
    return o;
  }).then(r=>{
    if(!r.committed){toast('Заявку уже взял другой волонтёр');view='home';render();return;}
    activeJob={id,...r.snapshot.val()};view='work';photoURL=null;render();
    toast('Заявка ваша — жилец видит, что вы в пути');
  }).catch(()=>toast('Не удалось взять заявку'));
}
function setStatus(s){
  if(!activeJob) return;
  const upd={status:s}; if(s==='done'){upd.doneAt=Date.now();upd.photo=!!photoURL;}
  db.ref('orders/'+activeJob.id).update(upd).then(()=>{
    if(s==='done'){activeJob=null;photoURL=null;view='fin';render();}
  }).catch(()=>toast('Не удалось обновить статус'));
}
function snap(inp){
  const f=inp.files&&inp.files[0]; if(!f) return;
  const r=new FileReader();
  r.onload=e=>{photoURL=e.target.result;render();toast('Фото прикреплено');};
  r.readAsDataURL(f);
}
function toggleAir(){
  onair=!onair; if(onair){beep();askNotify();} render();
  toast(onair?'Уведомления включены':'Уведомления выключены');
}

/* клиент: когда заявку закрыли — показать финал */
let lastPhase=-1;
setInterval(()=>{
  if(ROLE!=='cl') return;
  const done=Object.values(orders).some(o=>o.clientId===uid&&o.status==='done'&&
    Date.now()-(o.doneAt||0)<12000);
  if(done&&view==='live'){view='fin';render();}
},1500);

boot();
</script>
</body>
</html>
