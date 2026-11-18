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
  // The folder being viewed, relative to the scan root ("" = top level).
  // Kept in the URL hash so the browser's back button steps back up.
  function dirPath() {
    var m = /^#dir=(.*)$/.exec(location.hash);
    if (!m) return "";
    try { return decodeURIComponent(m[1]); } catch (e) { return ""; }
  }
  function openDir(path) {
    if (path === dirPath()) return;
    history.pushState(null, "", path ? "#dir=" + encodeURIComponent(path) :
      location.pathname + location.search);
    loadDirs();
  }

  function renderCrumbs(path) {
    var box = $("dirs-crumbs");
    if (!path) { box.hidden = true; box.innerHTML = ""; return; }
    var parts = path.split("/"), html = '<button type="button" data-path="">~</button>';
    parts.forEach(function (p, i) {
      var sub = parts.slice(0, i + 1).join("/");
      html += '<span class="sep" aria-hidden="true">/</span>' + (i === parts.length - 1 ?
        '<span class="here" aria-current="location">' + esc(p) + "</span>" :
        '<button type="button" data-path="' + esc(sub) + '">' + esc(p) + "</button>");
    });
    box.innerHTML = html; box.hidden = false;
  }

  // "frees X" when deleting would give back noticeably less than the size.
  function freesNote(bytes, freeable) {
    if (freeable === null || freeable === undefined || freeable >= bytes * 0.98) return "";
    return '<span class="frees' + (freeable < bytes * 0.02 ? " none" : "") + '">frees ' +
      fmtBytes(freeable) + "</span>";
  }

  function row(name, bytes, of, max, opts) {
    var share = of ? bytes / of * 100 : 0;
    var label = '<span class="name" title="' + esc(name) + '">' + esc(name) + "</span>";
    if (opts.path !== undefined) {
      label = '<button type="button" class="name link" data-path="' + esc(opts.path) +
        '" title="Open ' + esc(name) + '">' + esc(name) + '<span class="chev" aria-hidden="true">›</span></button>';
    }
    return '<li class="' + (opts.cls || "") + '">' + label +
      '<span class="size">' + fmtBytes(bytes) + '<span class="muted">' + share.toFixed(1) + "%</span>" +
      freesNote(bytes, opts.freeable) + "</span>" +
      '<span class="bar"><i style="width:' + (max ? bytes / max * 100 : 0).toFixed(1) + '%"></i></span></li>';
  }

  function renderDirs(d) {
    var list = $("dirs"), meta = $("dirs-meta");
    if (!d.enabled) {
      renderCrumbs("");
      list.innerHTML = '<li class="empty">Directory scanning is disabled in the config.</li>';
      meta.textContent = ""; return;
    }
    var scan = d.scan, lst = scan && scan.listing;
    meta.textContent = scan ? "Scanned " + ago(scan.ts) + (d.scanning ? " · rescanning…" : "") :
      (d.scanning ? "Scanning…" : "");
    if (d.error) meta.textContent += " · last scan failed: " + d.error;
    if (!lst) {
      renderCrumbs("");
      list.innerHTML = '<li class="empty">' + (d.scanning ? "First scan in progress…" :
        "No scan yet. The first scan starts about a minute after Headroom starts.") + "</li>";
      return;
    }
    // The requested folder may be gone after a rescan; the server then sends the top level.
    if (lst.path !== dirPath()) history.replaceState(null, "", lst.path ?
      "#dir=" + encodeURIComponent(lst.path) : location.pathname + location.search);
    renderCrumbs(lst.path);
    meta.textContent = fmtBytes(lst.bytes) + (lst.freeable !== null && lst.freeable !== undefined &&
      lst.freeable < lst.bytes * 0.98 ? " (frees " + fmtBytes(lst.freeable) + ")" : "") +
      " · " + meta.textContent;

    var items = lst.directories.map(function (x) {
      var o = x.expandable ? { path: x.path } : {};
      o.freeable = x.freeable;
      return { name: x.name, bytes: x.bytes, opts: o };
    });
    if (lst.loose > 0) items.push({ name: "(files directly in " + (lst.path ? "here" : "~") + ")",
      bytes: lst.loose, opts: { cls: "extra" } });
    if (lst.rest.count > 0) items.push({ name: lst.rest.count + " smaller folder" +
      (lst.rest.count === 1 ? "" : "s"), bytes: lst.rest.bytes, opts: { cls: "extra" } });
    // Folders come largest first from the server; the two summary rows stay at the bottom.
    if (!items.length) {
      list.innerHTML = '<li class="empty">This folder is empty.</li>'; return;
    }
    var max = Math.max.apply(null, items.map(function (x) { return x.bytes; })) || 1;
    list.innerHTML = items.map(function (x) { return row(x.name, x.bytes, lst.bytes, max, x.opts); }).join("");
  }

  function loadDirs() {
    return getJSON("api/directories?path=" + encodeURIComponent(dirPath())).then(renderDirs);
  }

  document.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest("#dirs [data-path], #dirs-crumbs [data-path]");
    if (b) openDir(b.getAttribute("data-path"));
  });
  window.addEventListener("popstate", loadDirs);


  /* ---------- space you could free ---------- */

  var SHOW = 8;
  var expanded = {};  // section id -> showing every item

  function ageText(ts) {
    var days = (Date.now() / 1000 - ts) / 86400;
    if (days < 1) return "changed today";
    if (days < 60) return "changed " + Math.round(days) + " days ago";
    if (days < 730) return "changed " + Math.round(days / 30.4) + " months ago";
    return "changed " + Math.round(days / 365) + " years ago";
  }
  // Paths are relative to the scan root; show "~/x" when that is the home folder.
  function showPath(root, p) { return root === "~" ? "~/" + p : p; }

  function freesLine(it) {
    if (it.freeable >= it.bytes * 0.98) return "";
    if (it.freeable < it.bytes * 0.02) return '<span class="frees none">hard-linked elsewhere: frees nothing</span>';
    return '<span class="frees">frees ' + fmtBytes(it.freeable) + " (rest is hard-linked elsewhere)</span>";
  }

  function item(title, size, subs, cls) {
    return '<li class="' + (cls || "") + '"><span class="name" title="' + esc(title) + '">' + esc(title) +
      '</span><span class="size">' + size + "</span>" +
      (subs.length ? '<span class="sub">' + subs.filter(Boolean).join('<span class="dot-sep" aria-hidden="true"> · </span>') + "</span>" : "") +
      "</li>";
  }

  function section(id, title, total, intro, rows, empty, open) {
    var all = expanded[id], shown = all ? rows : rows.slice(0, SHOW);
    var body = intro ? '<p class="muted small">' + intro + "</p>" : "";
    body += rows.length ? '<ol class="rlist">' + shown.join("") + "</ol>" :
      '<p class="muted small empty">' + empty + "</p>";
    if (rows.length > SHOW) {
      body += '<button type="button" class="more" data-more="' + id + '">' +
        (all ? "Show fewer" : "Show all " + rows.length) + "</button>";
    }
    return '<details class="rsec" data-sec="' + id + '"' + (open ? " open" : "") + "><summary><span>" + title +
      "</span>" + (total ? '<span class="rtotal">' + total + "</span>" : "") + "</summary>" + body + "</details>";
  }

  function sum(list, key) { return list.reduce(function (a, x) { return a + x[key]; }, 0); }

  function renderReclaim(d) {
    var box = $("reclaim"), meta = $("reclaim-meta"), btn = $("rescan");
    btn.disabled = !d.enabled || d.scanning;
    btn.textContent = d.scanning ? "Scanning…" : "Rescan now";
    meta.textContent = d.scan ? "Scanned " + ago(d.scan.ts) : "";
    if (!d.enabled) { box.innerHTML = '<p class="muted small">Directory scanning is disabled in the config.</p>'; return; }
    var r = d.report;
    if (!r) {
      box.innerHTML = '<p class="muted small">' + (d.scanning ? "Scan in progress…" :
        "No report yet. It is built by the next directory scan.") + "</p>";
      return;
    }
    // Keep whichever sections the user opened or closed across refreshes.
    var openState = {};
    Array.prototype.forEach.call(box.querySelectorAll("details[data-sec]"), function (el) {
      openState[el.getAttribute("data-sec")] = el.open;
    });
    function isOpen(id, dflt) { return id in openState ? openState[id] : dflt; }
    var root = r.root_label, html = root === "~" ? "" :
      '<p class="muted small">Paths are relative to <code>' + esc(root) + "</code>.</p>";

    // Leftover downloads
    var L = r.leftovers, lrows = [], intro, empty = "Nothing found: every entry belongs to a torrent.";
    if (!L.enabled) {
      intro = "Turn on <code>[transmission]</code> in the config to find downloads that no torrent uses any more.";
      empty = "Not checked.";
    } else if (L.error) {
      intro = '<span class="err">Could not read Transmission: ' + esc(L.error) + "</span>";
      empty = "Not checked.";
    } else {
      intro = "In Transmission's download folders (" + L.folders.map(function (f) {
        return "<code>" + esc(showPath(root, f)) + "</code>"; }).join(", ") +
        ") but not part of " + (L.torrents === 1 ? "its only torrent" : "any of its " + L.torrents + " torrents") + ". Usually data from torrents removed without their files.";
      if (L.unscanned.length) intro += " Not checked, because they are outside the scanned folder: " +
        L.unscanned.map(function (f) { return "<code>" + esc(f) + "</code>"; }).join(", ") + ".";
      lrows = L.items.map(function (it) {
        return item(showPath(root, it.path) + (it.is_dir ? "/" : ""), fmtBytes(it.bytes),
          [ageText(it.mtime), freesLine(it)]);
      });
    }
    html += section("leftovers", "Leftover downloads", lrows.length ? "frees " + fmtBytes(sum(L.items, "freeable")) : "",
      intro, lrows, empty, isOpen("leftovers", true));

    // Caches and logs
    html += section("junk", "Caches, logs and trash", r.junk.length ? "frees " + fmtBytes(sum(r.junk, "freeable")) : "",
      "Folders that apps rebuild on their own. Stop the app before emptying its folder.",
      r.junk.map(function (j) {
        return item(showPath(root, j.path), fmtBytes(j.bytes), [esc(j.label), freesLine(j)]);
      }), "None of the known cache folders take up space.", isOpen("junk", true));

    // Largest files
    html += section("files", "Largest files", "",
      "Files with more than one link are shared with another path, usually your media library: deleting one copy frees nothing.",
      r.files.map(function (f) {
        return item(showPath(root, f.path), fmtBytes(f.bytes),
          [f.links > 1 ? '<span class="frees none">' + f.links + " links: frees nothing on its own</span>" : ""]);
      }), "No files found.", isOpen("files", false));

    // Likely duplicates
    html += section("dups", "Likely duplicates", r.duplicates.length ? "up to " + fmtBytes(sum(r.duplicates, "saving")) : "",
      "Separate files with the same size and the same content at the start, middle and end. Check before deleting one.",
      r.duplicates.map(function (g) {
        return item(g.paths.length + " copies of " + fmtBytes(g.bytes), "saves " + fmtBytes(g.saving),
          g.paths.map(function (p) { return '<code>' + esc(showPath(root, p)) + "</code>"; }), "dup");
      }), "No duplicates found.", isOpen("dups", false));

    if (r.errors) html += '<p class="muted small">' + r.errors + " items could not be read and were skipped.</p>";
    box.innerHTML = html;
  }

  function loadReclaim() {
    return getJSON("api/reclaim").then(renderReclaim);
  }

  $("reclaim").addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest("[data-more]");
    if (!b) return;
    var id = b.getAttribute("data-more");
    expanded[id] = !expanded[id];
    loadReclaim();
  });

  var pollTimer;
  function pollScan() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(function () {
      getJSON("api/reclaim").then(function (d) {
        renderReclaim(d);
        if (d.scanning) pollScan(); else loadDirs();
      });
    }, 5000);
  }

  $("rescan").addEventListener("click", function () {
    var btn = $("rescan");
    btn.disabled = true; btn.textContent = "Scanning…";
    fetch("api/rescan", { method: "POST", credentials: "same-origin", headers: { "X-Headroom": "1" } })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); pollScan(); })
      .catch(function (e) { btn.disabled = false; btn.textContent = "Rescan now"; $("reclaim-meta").textContent = "Could not start scan: " + e.message; });
  });

  /* ---------- wiring ---------- */

  function refresh() {
    return Promise.all([
      getJSON("api/status").then(renderStatus),
      loadHistory(),
      loadDirs(),
      loadReclaim()
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
