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

  // ---- menu lateral (celular) ----
  var app = d.querySelector('.app');
  d.addEventListener('click', function (e) {
    if (e.target.closest('#menu-btn')) app.classList.toggle('menu-aberto');
    else if (e.target.closest('#veu')) app.classList.remove('menu-aberto');
  });

  // ---- conversas ----
  var chat = d.getElementById('chat');
  function rolarFim(el, forcar) {
    if (!el) return;
    if (forcar || el.scrollHeight - el.scrollTop - el.clientHeight < 140) el.scrollTop = el.scrollHeight;
  }
  d.body.addEventListener('htmx:beforeSwap', function (e) {
    var t = e.detail.target; if (t && t.id === 'c-msgs') t._perto = t.scrollHeight - t.scrollTop - t.clientHeight < 140;
  });
  d.body.addEventListener('htmx:afterSwap', function (e) {
    var t = e.detail.target; if (!t) return;
    if (t.id === 'c-conv') {
      if (chat) chat.classList.add('em-conversa');
      rolarFim(d.getElementById('c-msgs'), true);
      var ta = d.querySelector('#c-form textarea'); if (ta && !ta.disabled && matchMedia('(hover:hover)').matches) ta.focus();
      var m = d.querySelector('#c-conv .c-topo h2');
      d.querySelectorAll('.c-item').forEach(function (b) { b.classList.remove('ativo'); });
    } else if (t.id === 'c-msgs') { rolarFim(t, !!t._perto); }
  });
  d.addEventListener('keydown', function (e) {
    var ta = e.target.closest && e.target.closest('#c-form textarea');
    if (ta && e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (ta.value.trim()) htmx.trigger(ta.form, 'submit'); }
  });
  d.addEventListener('change', function (e) {
    var s = e.target.closest('#c-rapidas'); if (!s || !s.value) return;
    var ta = d.querySelector('#c-form textarea');
    if (ta && !ta.disabled) { ta.value = s.value; ta.focus(); } else { try { navigator.clipboard.writeText(s.value); mostrar('Texto copiado'); } catch (x) {} }
    s.value = '';
  });
  d.addEventListener('input', function (e) {
    var ta = e.target.closest('#c-form textarea'); if (!ta) return; ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 140) + 'px';
  });
  d.addEventListener('click', function (e) {
    var it = e.target.closest('.c-item'); if (!it) return;
    d.querySelectorAll('.c-item').forEach(function (b) { b.classList.toggle('ativo', b === it); });
    it.classList.remove('nl'); var un = it.querySelector('.un'); if (un) un.remove();
    try { history.replaceState(null, '', '/conversas/?lead=' + it.dataset.lead); } catch (x) {}
  });
  d.addEventListener('DOMContentLoaded', function () { rolarFim(d.getElementById('c-msgs'), true); });

  // ---- barra de ações da conversa (estilo Avise Aí) ----
  d.addEventListener('click', function (e) {
    var b = e.target.closest('[data-preencher]');
    if (b) {
      fetch(b.dataset.preencher, { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (j) {
        var ta = d.querySelector('#c-form textarea');
        if (j.erro) { mostrar(j.erro); return; }
        if (ta && !ta.disabled) { ta.value = j.texto; ta.dispatchEvent(new Event('input')); ta.focus(); }
        else { try { navigator.clipboard.writeText(j.texto); mostrar('Texto copiado (a conversa está fora da janela de 24 h)'); } catch (x) {} }
      });
      return;
    }
    var a = e.target.closest('[data-abrir]');
    if (a) {
      var alvo = d.querySelector(a.dataset.abrir); if (!alvo) return;
      if (alvo.tagName === 'DETAILS') { var pai = alvo.closest('.mais-ferr'); if (pai) pai.open = true; alvo.open = !alvo.open; alvo.scrollIntoView({ block: 'nearest' }); }
      else { alvo.hidden = !alvo.hidden; var inp = alvo.querySelector('input[type=text]'); if (inp && !alvo.hidden) inp.focus(); }
      return;
    }
    var em = e.target.closest('[data-emoji]');
    if (em) {
      var t = d.querySelector('#c-form textarea');
      if (t && !t.disabled) { var i = t.selectionStart || t.value.length; t.value = t.value.slice(0, i) + em.dataset.emoji + t.value.slice(i); t.focus(); }
      var det = em.closest('details'); if (det) det.open = false;
      return;
    }
    // fecha menus (pop) ao clicar fora
    d.querySelectorAll('details.pop[open]').forEach(function (p) { if (!p.contains(e.target)) p.open = false; });
  });
})();
