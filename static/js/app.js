// Small progressive-enhancement helpers. No inline scripts (strict CSP).
(function () {
  document.documentElement.classList.add("js");

  // Scroll reveal: elements with .reveal fade up as they enter the viewport (CSS in input.css).
  function observeReveals(root) {
    var items = (root || document).querySelectorAll(".reveal:not(.revealed)");
    if (!("IntersectionObserver" in window)) {
      items.forEach(function (el) { el.classList.add("revealed"); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) { entry.target.classList.add("revealed"); io.unobserve(entry.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    items.forEach(function (el) { io.observe(el); });
  }
  document.addEventListener("DOMContentLoaded", function () { observeReveals(); });
  document.addEventListener("htmx:afterSwap", function (e) { observeReveals(e.target); });

  // Toasts close themselves after a few seconds (hovering keeps them open).
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-message][data-autodismiss]").forEach(function (el) {
      var timer = window.setTimeout(function () { el.remove(); }, 6000);
      el.addEventListener("mouseenter", function () { window.clearTimeout(timer); });
    });
  });

  // Popups opened with [data-toggle] close on Escape or a click outside them.
  function closePopups(except) {
    document.querySelectorAll("[data-toggle][aria-expanded=true]").forEach(function (btn) {
      var target = document.getElementById(btn.getAttribute("data-toggle"));
      if (!target || target === except || !target.hasAttribute("data-popup")) return;
      target.classList.add("hidden");
      btn.setAttribute("aria-expanded", "false");
    });
  }
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closePopups(null); });
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-toggle]") || e.target.closest("[data-popup]")) return;
    closePopups(null);
  });
  // Confirmation dialogs for destructive actions: <form data-confirm="Are you sure?">
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var msg = form.getAttribute && form.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, true);

  // Mobile navigation toggle
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-toggle]");
    if (!btn) return;
    var target = document.getElementById(btn.getAttribute("data-toggle"));
    if (!target) return;
    var hidden = target.classList.toggle("hidden");
    btn.setAttribute("aria-expanded", hidden ? "false" : "true");
  });

  // Auto-submit filter selects (HTMX forms handle the request)
  document.addEventListener("change", function (e) {
    if (e.target.matches("[data-autosubmit] select, [data-autosubmit] input[type=checkbox], [data-autosubmit] input[type=radio]")) {
      var form = e.target.form;
      if (form && window.htmx) { window.htmx.trigger(form, "submit"); }
    }
  });

  // Copy-to-clipboard buttons: <button data-copy="#id">
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy]");
    if (!btn) return;
    var el = document.querySelector(btn.getAttribute("data-copy"));
    if (el && navigator.clipboard) {
      navigator.clipboard.writeText(el.innerText).then(function () { btn.textContent = "Copied"; });
    }
  });

  // Dismiss flash messages
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-dismiss]");
    if (btn) { var el = btn.closest("[data-message]"); if (el) el.remove(); }
  });

  // Django formsets: <button data-formset-add="prefix"> clones <template id="prefix-template">,
  // <button data-formset-remove> ticks the item's DELETE box and hides it.
  document.addEventListener("click", function (e) {
    var add = e.target.closest("[data-formset-add]");
    if (add) {
      var prefix = add.getAttribute("data-formset-add");
      var total = document.getElementById("id_" + prefix + "-TOTAL_FORMS");
      var tpl = document.getElementById(prefix + "-template");
      var list = document.getElementById(prefix + "-forms");
      if (!total || !tpl || !list) return;
      var index = parseInt(total.value, 10);
      var wrap = document.createElement("div");
      wrap.innerHTML = tpl.innerHTML.replace(/__prefix__/g, index);
      var item = wrap.firstElementChild;
      list.appendChild(item);
      total.value = index + 1;
      var first = item.querySelector("input:not([type=hidden]), textarea");
      if (first) first.focus();
      return;
    }
    var remove = e.target.closest("[data-formset-remove]");
    if (remove) {
      var row = remove.closest("[data-formset-item]");
      var del = row && row.querySelector("input[name$='-DELETE']");
      if (del) { del.checked = true; row.style.display = "none"; }
    }
  });

  // CV builder: show job-description fields only for the "from job description" mode
  document.addEventListener("change", function (e) {
    if (!e.target.matches("[data-mode-group] input[name=mode]")) return;
    var jd = e.target.value === "jd";
    document.querySelectorAll("[data-jd-only]").forEach(function (el) { el.classList.toggle("hidden", !jd); });
  });

  // Long-running forms: <form data-loading> with <button data-loading-text="…">
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches || !form.matches("[data-loading]")) return;
    var btn = form.querySelector("[data-loading-text]");
    if (btn) {
      btn.textContent = btn.getAttribute("data-loading-text");
      window.setTimeout(function () { btn.disabled = true; }, 0);  // after the submit is under way
    }
  });

  // htmx config: no inline indicator styles (CSP), send CSRF token on unsafe requests.
  document.addEventListener("htmx:configRequest", function (e) {
    var token = document.querySelector('meta[name="csrf-token"]');
    if (token) e.detail.headers["X-CSRFToken"] = token.content;
  });
})();
