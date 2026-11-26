(function () {
  var d = document, dlg = d.getElementById('dlg'), box = d.getElementById('dlg-box'), toast = d.getElementById('toast');
  var ultimo = null, ctx = null, tocar = false;
  try { tocar = localStorage.getItem('som') === '1'; } catch (e) {}

  function csrf() { return (d.body.getAttribute('hx-headers') || '').match(/"X-CSRFToken":\s*"([^"]+)"/)?.[1] || ''; }
  function mostrar(msg) {
    toast.textContent = msg; toast.hidden = false;
    clearTimeout(mostrar.t); mostrar.t = setTimeout(function () { toast.hidden = true; }, 5000);
  }
  function beep() {
    if (!tocar) return;
    try {
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      [0, 0.22].forEach(function (atraso) {
        var o = ctx.createOscillator(), g = ctx.createGain();
        o.frequency.value = 880; o.connect(g); g.connect(ctx.destination);
        g.gain.setValueAtTime(0.2, ctx.currentTime + atraso); g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + atraso + 0.18);
        o.start(ctx.currentTime + atraso); o.stop(ctx.currentTime + atraso + 0.2);
      });
    } catch (e) {}
  }

  // erros de ações (ex.: envio pela API recusado) aparecem como aviso
  d.body.addEventListener('htmx:responseError', function (e) { var t = (e.detail.xhr && e.detail.xhr.responseText) || ''; if (t && t.length < 300) mostrar(t); });

  // ---- diálogo (painel do lead e formulários) ----
  d.body.addEventListener('htmx:afterSwap', function (e) {
    if (e.detail.target === box && !dlg.open) dlg.showModal();
  });
  d.body.addEventListener('fecharDlg', function () { if (dlg.open) dlg.close(); });
  dlg.addEventListener('close', function () { box.innerHTML = ''; htmx.trigger(d.body, 'quadroAtualizar'); });
  dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });

  function rotuloSom(b) { b.classList.toggle('on', tocar); var t = b.querySelector('span'); if (t) t.textContent = tocar ? 'Som ligado' : 'Som desligado'; }
  // ---- som ----
  d.addEventListener('click', function (e) {
    var b = e.target.closest('#som'); if (!b) return;
    tocar = !tocar; try { localStorage.setItem('som', tocar ? '1' : '0'); } catch (x) {}
    rotuloSom(b); if (tocar) beep();
  });

  // ---- WhatsApp: abre a conversa e marca o atendimento ----
  d.addEventListener('click', function (e) {
    var a = e.target.closest('.js-wa'); if (!a) return;
    try {
      fetch(a.dataset.marcar, { method: 'POST', keepalive: true, headers: { 'X-CSRFToken': csrf() } })
        .then(function () { htmx.trigger(d.body, 'quadroAtualizar'); });
    } catch (x) {}
  });

  // ---- abas (celular) ----
  function aba(etapa) {
    d.querySelectorAll('.abas button').forEach(function (b) { b.classList.toggle('ativa', b.dataset.etapa === etapa); });
    d.querySelectorAll('.col').forEach(function (c) { c.classList.toggle('ativa', c.dataset.etapa === etapa); });
    try { localStorage.setItem('aba', etapa); } catch (x) {}
  }
  d.addEventListener('click', function (e) { var b = e.target.closest('.abas button'); if (b) aba(b.dataset.etapa); });

  // ---- arrastar (computador) ----
  function iniciarSortable(raiz) {
    if (!window.Sortable) return;
    raiz.querySelectorAll('.lista').forEach(function (el) {
      Sortable.create(el, {
        group: 'leads', animation: 150, delay: 180, delayOnTouchOnly: true, ghostClass: 'sortable-ghost', chosenClass: 'sortable-chosen',
        onStart: function () { window.__arrastando = true; },
        onEnd: function (ev) {
          window.__arrastando = false;
          var de = ev.from.dataset.etapa, para = ev.to.dataset.etapa, card = ev.item;
          if (de === para) return;
          var comForm = (raiz.dataset.comForm || '').split(',');
          if (comForm.indexOf(para) >= 0) {
            htmx.ajax('GET', card.dataset.moverUrl + '?etapa=' + para, { target: '#dlg-box', swap: 'innerHTML' });
          } else {
            htmx.ajax('POST', card.dataset.diretoUrl, { values: { etapa: para }, swap: 'none' });
          }
        }
      });
    });
  }

  // ---- SLA ao vivo (verde < 5 min, amarelo < 30, vermelho acima) ----
  function atualizarSla() {
    var agora = Date.now() / 1000;
    d.querySelectorAll('.card[data-sla]').forEach(function (c) {
      var s = agora - Number(c.dataset.ts), el = c.querySelector('.sla'); if (!el) return;
      var m = Math.floor(s / 60);
      el.className = 'sla ' + (s < 300 ? 'ok' : s < 1800 ? 'warn' : 'late');
      el.textContent = m < 60 ? m + ' min' : Math.floor(m / 60) + ' h';
    });
  }
  setInterval(atualizarSla, 15000);

  d.body.addEventListener('htmx:beforeRequest', function (e) {
    if (window.__arrastando && e.detail.elt && e.detail.elt.id === 'quadro') e.preventDefault();
  });

  d.body.addEventListener('htmx:afterSwap', function (e) {
    var q = e.detail.target; if (!q || q.id !== 'quadro') return;
    var dados = q.querySelector('#qdados');
    var novo = dados ? Number(dados.dataset.ultimo) : 0;
    if (ultimo !== null && novo > ultimo) { mostrar('Novo lead chegou!'); beep(); d.title = '(●) Novo lead · TopCar'; setTimeout(function () { d.title = 'TopCar · Leads'; }, 8000); }
    ultimo = novo;
    var raiz = q.querySelector('.quadro'); if (raiz) iniciarSortable(raiz);
    var salva = null; try { salva = localStorage.getItem('aba'); } catch (x) {}
    aba(salva && q.querySelector('.col[data-etapa="' + salva + '"]') ? salva : 'novo');
    atualizarSla();
  });

  var som = d.getElementById('som'); if (som) rotuloSom(som);
  d.addEventListener('click', function (e) { var f = e.target.closest('#filtrar'); if (!f) return; var barra = d.getElementById('filtros'); var aberto = barra.classList.toggle('abrir'); f.setAttribute('aria-expanded', aberto); });
})();
