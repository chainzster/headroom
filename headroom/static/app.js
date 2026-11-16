/* Headroom front end: no dependencies, no build step. */
(function () {
  "use strict";

  var REFRESH_MS = 60 * 1000;
  var range = "7d";
  var thresholds = { warning: 80, high: 90, critical: 95 };
  var LEVELS = {
    ok: { icon: "✓", text: "Healthy" },
    warning: { icon: "!", text: "Warning" },
    high: { icon: "!", text: "High usage" },
    critical: { icon: "✕", text: "Critical" },
    unknown: { icon: "?", text: "No data" }
  };

  function $(id) { return document.getElementById(id); }

  function fmtBytes(b, digits) {
    if (b === null || b === undefined || isNaN(b)) return "–";
    var neg = b < 0; b = Math.abs(b);
    var units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"], i = 0;
    while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
    var d = digits !== undefined ? digits : (b >= 100 || i === 0 ? 0 : b >= 10 ? 1 : 2);
    return (neg ? "-" : "") + b.toFixed(d) + " " + units[i];
  }
  function fmtChange(b) {
    if (b === undefined || b === null) return "–";
    return (b > 0 ? "+" : b < 0 ? "−" : "±") + fmtBytes(Math.abs(b));
  }
  function fmtTime(ts, withDate) {
    var d = new Date(ts * 1000);
    var t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (!withDate) return t;
    return d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " + t;
  }
  function ago(ts) {
    var s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    if (s < 90) return s + "s ago";
    if (s < 5400) return Math.round(s / 60) + " min ago";
    if (s < 129600) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " days ago";
  }
  function getJSON(url) {
    return fetch(url, { credentials: "same-origin", cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(url + ": HTTP " + r.status);
      return r.json();
    });
  }

  /* ---------- status ---------- */

  function renderStatus(s) {
    thresholds = s.thresholds;
    var r = s.reading, lvl = s.level || "unknown", info = LEVELS[lvl];
    var pill = $("level");
    pill.className = "pill pill-" + lvl;
    pill.querySelector(".pill-icon").textContent = info.icon;
    pill.querySelector(".pill-text").textContent = info.text;

    var fill = $("meter-fill");
    fill.className = "meter-fill lvl-" + lvl;
    var ticks = "";
    ["warning", "high", "critical"].forEach(function (k) {
      ticks += '<span class="tick" style="left:calc(' + thresholds[k] + '% - 1px)" title="' + k + " " + thresholds[k] + '%"></span>';
    });
    $("meter-ticks").innerHTML = ticks;

    var alert = $("alert"), msgs = [];
    if (s.monitor && s.monitor.error) msgs.push("Could not read quota: " + s.monitor.error);
    if (r) {
      $("free").textContent = fmtBytes(r.free);
      $("percent").textContent = r.percent.toFixed(1) + "%";
      fill.style.width = Math.min(100, r.percent) + "%";
      $("used-of").textContent = fmtBytes(r.used) + " of " + fmtBytes(r.total) + " used";
      $("used").textContent = fmtBytes(r.used);
      $("total").textContent = fmtBytes(r.total);
      $("chg24").textContent = fmtChange(s.change["24h"]);
      $("chg7").textContent = fmtChange(s.change["7d"]);
      $("updated").textContent = "Last reading " + ago(r.ts) + " (" + fmtTime(r.ts, true) + ")";
      document.title = r.percent.toFixed(0) + "% · Headroom";
      if (lvl === "warning" || lvl === "high" || lvl === "critical") {
        msgs.unshift(info.text + ": " + r.percent.toFixed(1) + "% of your allocation is used (threshold " +
          thresholds[lvl] + "%). " + fmtBytes(r.free) + " remaining.");
      }
    } else {
      $("updated").textContent = "Waiting for first reading…";
    }
    alert.hidden = msgs.length === 0;
    alert.className = "alert lvl-" + (s.monitor && s.monitor.error ? "critical" : lvl);
    alert.textContent = msgs.join(" ");
  }

  /* ---------- history chart (inline SVG) ---------- */

  var SVGNS = "http://www.w3.org/2000/svg";
  function el(name, attrs, parent) {
    var e = document.createElementNS(SVGNS, name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function fmtAxis(v, unit) {
    var units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
    var n = v / unit;
    return (n % 1 ? n.toFixed(1) : n) + " " + units[Math.round(Math.log(unit) / Math.log(1024))];
  }
  function niceStep(max, count) {
    var raw = max / count, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    var n = raw / mag;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * mag;
  }

  function renderChart(points) {
    var box = $("chart");
    box.innerHTML = "";
    var W = box.clientWidth || 600, H = box.clientHeight || 240;
    var m = { l: 64, r: 8, t: 8, b: 24 };
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img",
      "aria-label": "Used storage over the selected range" }, box);

    if (!points.length) {
      el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", "class": "empty" }, svg)
        .textContent = "No readings in this range yet";
      return;
    }

    var t0 = points[0][0], t1 = points[points.length - 1][0];
    if (t1 === t0) { t0 -= 1800; t1 += 1800; }
    var yMax = 0;
    points.forEach(function (p) { yMax = Math.max(yMax, p[1], p[2]); });
    var unit = Math.pow(1024, Math.max(0, Math.floor(Math.log(yMax || 1) / Math.log(1024))));
    var step = niceStep(yMax * 1.05 / unit, 5) * unit;
    var top = Math.ceil(yMax * 1.05 / step) * step || 1;

    var pw = W - m.l - m.r, ph = H - m.t - m.b;
    function x(t) { return m.l + (t - t0) / (t1 - t0) * pw; }
    function y(v) { return m.t + ph - v / top * ph; }

    var axis = el("g", { "class": "axis" }, svg);
    for (var v = 0; v <= top + 1; v += step) {
      el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), "class": "gridline" }, axis);
      el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, axis).textContent = fmtAxis(v, unit);
    }
    var span = t1 - t0, withDate = span > 86400 * 1.5, ticks = Math.max(2, Math.floor(pw / 110));
    for (var i = 0; i <= ticks; i++) {
      var t = t0 + span * i / ticks;
      el("text", { x: x(t), y: H - 6, "text-anchor": i === 0 ? "start" : i === ticks ? "end" : "middle" }, axis)
        .textContent = withDate ? new Date(t * 1000).toLocaleDateString([], { month: "short", day: "numeric" }) : fmtTime(t);
    }

    var line = "", ref = "";
    points.forEach(function (p, i) {
      line += (i ? "L" : "M") + x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1);
      ref += (i ? "L" : "M") + x(p[0]).toFixed(1) + "," + y(p[2]).toFixed(1);
    });
    var base = y(0).toFixed(1);
    el("path", { d: line + "L" + x(points[points.length - 1][0]).toFixed(1) + "," + base +
      "L" + x(points[0][0]).toFixed(1) + "," + base + "Z", "class": "area" }, svg);
    el("path", { d: ref, "class": "ref" }, svg);
    el("path", { d: line, "class": "line" }, svg);

    // Crosshair + tooltip
    var cross = el("line", { y1: m.t, y2: m.t + ph, "class": "cross", visibility: "hidden" }, svg);
    var dot = el("circle", { r: 4.5, "class": "dot", visibility: "hidden" }, svg);
    var tip = document.createElement("div");
    tip.className = "tip"; tip.hidden = true; box.appendChild(tip);
    var hit = el("rect", { x: m.l, y: m.t, width: pw, height: ph, fill: "transparent" }, svg);

    function move(ev) {
      var rect = svg.getBoundingClientRect();
      var px = (ev.clientX - rect.left) * (W / rect.width);
      var t = t0 + (px - m.l) / pw * (t1 - t0), best = points[0];
      for (var i = 1; i < points.length; i++) {
        if (Math.abs(points[i][0] - t) < Math.abs(best[0] - t)) best = points[i];
      }
      var cx = x(best[0]), cy = y(best[1]);
      cross.setAttribute("x1", cx); cross.setAttribute("x2", cx);
      dot.setAttribute("cx", cx); dot.setAttribute("cy", cy);
      cross.setAttribute("visibility", "visible"); dot.setAttribute("visibility", "visible");
      var pct = best[2] ? (best[1] / best[2] * 100).toFixed(1) + "%" : "";
      tip.innerHTML = "<b>" + fmtBytes(best[1]) + "</b> used · " + pct +
        '<span class="muted">of ' + fmtBytes(best[2]) + " · " + fmtTime(best[0], true) + "</span>";
      tip.hidden = false;
      var sx = cx * rect.width / W, tw = tip.offsetWidth;
      tip.style.left = Math.max(0, Math.min(rect.width - tw, sx - tw / 2)) + "px";
      tip.style.top = Math.max(0, cy * rect.height / H - tip.offsetHeight - 12) + "px";
    }
    function leave() {
      tip.hidden = true;
      cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden");
    }
    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerdown", move);
    hit.addEventListener("pointerleave", leave);
  }

  var lastPoints = [];
  function loadHistory() {
    return getJSON("api/history?range=" + encodeURIComponent(range)).then(function (d) {
      lastPoints = d.points; renderChart(lastPoints);
    });
  }

  /* ---------- directories ---------- */

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function renderDirs(d) {
    var list = $("dirs"), meta = $("dirs-meta");
    if (!d.enabled) {
      list.innerHTML = '<li class="empty">Directory scanning is disabled in the config.</li>';
      meta.textContent = ""; return;
    }
    var scan = d.scan;
    meta.textContent = scan ? "Scanned " + ago(scan.ts) + " · " + fmtBytes(scan.total) + " total" +
      (d.scanning ? " · rescanning…" : "") : (d.scanning ? "Scanning…" : "");
    if (d.error) meta.textContent += " · last scan failed: " + d.error;
    if (!scan || !scan.directories.length) {
      list.innerHTML = '<li class="empty">' + (d.scanning ? "First scan in progress…" :
        "No scan yet. The first scan starts about a minute after Headroom starts.") + "</li>";
      return;
    }
    var max = scan.directories[0].bytes || 1, html = "";
    scan.directories.forEach(function (x) {
      var share = scan.total ? (x.bytes / scan.total * 100) : 0;
      html += '<li><span class="name" title="' + esc(x.name) + '">' + esc(x.name) + "</span>" +
        '<span class="size">' + fmtBytes(x.bytes) + '<span class="muted">' + share.toFixed(1) + "%</span></span>" +
        '<span class="bar"><i style="width:' + (x.bytes / max * 100).toFixed(1) + '%"></i></span></li>';
    });
    list.innerHTML = html;
  }

  /* ---------- wiring ---------- */

  function refresh() {
    return Promise.all([
      getJSON("api/status").then(renderStatus),
      loadHistory(),
      getJSON("api/directories").then(renderDirs)
    ]).catch(function (e) {
      var a = $("alert");
      a.hidden = false; a.className = "alert lvl-critical";
      a.textContent = "Could not reach Headroom: " + e.message;
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll(".seg button"), function (b) {
    b.addEventListener("click", function () {
      range = b.getAttribute("data-range");
      Array.prototype.forEach.call(document.querySelectorAll(".seg button"), function (o) {
        o.classList.toggle("active", o === b);
      });
      loadHistory();
    });
  });

  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { renderChart(lastPoints); }, 150);
  });

  refresh();
  setInterval(function () { if (!document.hidden) refresh(); }, REFRESH_MS);
})();
