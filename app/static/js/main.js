document.addEventListener('DOMContentLoaded', function () {
  // Dropdowns genericos do menu no topo (categorias, usuario, regiao):
  // qualquer botao com data-toggle="algumId" abre/fecha o elemento daquele id.
  //
  // No desktop o dropdown vira "position: fixed" calculado a partir do botao
  // (em vez de "absolute" dentro de .gc-nav-categories), porque essa faixa
  // tem overflow-x:auto pra caber muitas categorias numa linha so — e um
  // "absolute" dentro de um pai com overflow:auto fica cortado (some da tela).
  function closeDropdown(el) {
    el.classList.remove('open');
    el.style.position = '';
    el.style.top = '';
    el.style.left = '';
    el.style.right = '';
    var wrap = el.closest('.gc-nav-cat, .gc-header-user, .gc-nav-region');
    if (wrap) wrap.classList.remove('open');
  }

  function closeAllDropdowns(except) {
    document.querySelectorAll('.gc-dropdown.open').forEach(function (el) {
      if (el !== except) closeDropdown(el);
    });
  }

  document.querySelectorAll('[data-toggle]').forEach(function (btn) {
    var targetId = btn.getAttribute('data-toggle');
    var target = document.getElementById(targetId);
    if (!target) return;
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var willOpen = !target.classList.contains('open');
      closeAllDropdowns(target);
      if (willOpen && window.innerWidth > 900) {
        var r = btn.getBoundingClientRect();
        target.style.position = 'fixed';
        target.style.top = (r.bottom + 4) + 'px';
        if (target.classList.contains('gc-dropdown-right')) {
          target.style.left = 'auto';
          target.style.right = (window.innerWidth - r.right) + 'px';
        } else {
          target.style.left = r.left + 'px';
          target.style.right = 'auto';
        }
      } else if (!willOpen) {
        target.style.position = '';
        target.style.top = '';
        target.style.left = '';
        target.style.right = '';
      }
      target.classList.toggle('open', willOpen);
      var wrap = target.closest('.gc-nav-cat, .gc-header-user, .gc-nav-region');
      if (wrap) wrap.classList.toggle('open', willOpen);
    });
  });
  document.addEventListener('click', function (e) {
    if (e.target.closest('.gc-dropdown') || e.target.closest('[data-toggle]')) return;
    closeAllDropdowns();
  });

  // Menu no topo: no celular vira uma gaveta acionada pelo hamburguer.
  var navToggle = document.getElementById('gcNavToggle');
  var gcNav = document.getElementById('gcNav');
  if (navToggle && gcNav) {
    navToggle.addEventListener('click', function (e) {
      e.stopPropagation();
      gcNav.classList.toggle('open');
    });
  }

  var pwInput = document.getElementById('registerPassword');
  var pwChecklist = document.getElementById('passwordChecklist');
  if (pwInput && pwChecklist) {
    var rules = {
      upper: function (v) { return /[A-Z]/.test(v); },
      number: function (v) { return /[0-9]/.test(v); },
      symbol: function (v) { return /[^A-Za-z0-9]/.test(v); },
    };
    pwInput.addEventListener('input', function () {
      var value = pwInput.value;
      Object.keys(rules).forEach(function (key) {
        var el = pwChecklist.querySelector('.pw-rule[data-rule="' + key + '"]');
        if (!el) return;
        el.classList.toggle('met', rules[key](value));
      });
    });
  }

  document.querySelectorAll('.toggle-password').forEach(function (btn) {
    var target = document.getElementById(btn.getAttribute('data-target'));
    if (!target) return;
    btn.addEventListener('click', function () {
      var showing = target.type === 'text';
      target.type = showing ? 'password' : 'text';
      btn.classList.toggle('showing', !showing);
    });
  });

  var loginEmailField = document.getElementById('loginEmailField');
  var loginForm = document.getElementById('loginForm');
  if (loginEmailField && loginForm) {
    try {
      var lembrado = localStorage.getItem('gc_last_login_email');
      if (lembrado) loginEmailField.value = lembrado;
    } catch (e) { /* localStorage indisponivel, segue sem lembrar */ }

    loginForm.addEventListener('submit', function () {
      try {
        localStorage.setItem('gc_last_login_email', loginEmailField.value.trim());
      } catch (e) { /* ignora */ }
    });
  }
});
