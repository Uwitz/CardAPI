/* UWITZ CARDS — legal page chrome (ported from uwitz.js mount logic).
   Header/footer are server-rendered; this handles the mobile menu and
   scroll-spy highlighting for the "On this page" sidebar. */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    var burger = document.getElementById('js-burger');
    var panel = document.getElementById('js-mobile-nav');

    if (burger && panel) {
      burger.addEventListener('click', function () {
        var open = burger.getAttribute('aria-expanded') === 'true';
        burger.setAttribute('aria-expanded', String(!open));
        burger.setAttribute('aria-label', open ? 'Open menu' : 'Close menu');
        burger.classList.toggle('is-open', !open);
        panel.hidden = open;
      });

      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && burger.getAttribute('aria-expanded') === 'true') burger.click();
      });

      window.matchMedia('(min-width: 721px)').addEventListener('change', function (e) {
        if (e.matches && burger.getAttribute('aria-expanded') === 'true') burger.click();
      });
    }

    /* ── Scroll-spy: highlight the sidebar link for the section
       currently in view (matches .legal-sidebar-nav a.active). ── */
    var links = Array.prototype.slice.call(
      document.querySelectorAll('.legal-sidebar-nav a[href^="#"]')
    );
    if (!links.length) return;

    var targets = links
      .map(function (a) {
        var id = a.getAttribute('href').slice(1);
        var el = id && document.getElementById(id);
        return el ? { link: a, el: el } : null;
      })
      .filter(Boolean);
    if (!targets.length) return;

    var HEADER_OFFSET = 100; /* sticky header 56px + breathing room, matches sidebar top */
    var ticking = false;

    function setActive(idx) {
      targets.forEach(function (t, i) {
        t.link.classList.toggle('active', i === idx);
      });
    }

    function update() {
      ticking = false;
      var idx = 0;
      for (var i = 0; i < targets.length; i++) {
        if (targets[i].el.getBoundingClientRect().top <= HEADER_OFFSET) idx = i;
      }
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) {
        idx = targets.length - 1;
      }
      setActive(idx);
    }

    function onScroll() {
      if (!ticking) {
        ticking = true;
        window.requestAnimationFrame(update);
      }
    }

    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });

    if (location.hash) {
      var h = location.hash.slice(1);
      for (var j = 0; j < targets.length; j++) {
        if (targets[j].el.id === h) { setActive(j); break; }
      }
    } else {
      setActive(0);
    }
    update();
  });
})();
