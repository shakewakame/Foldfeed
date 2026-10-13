/* 3D viewer for the article pages: Mol* (assets/vendor/molstar), loaded only when the reader asks for it.
 *
 * Markup (pipeline/site.py): .v3d[data-pdb][data-ents][data-ligs][data-assets] holding the static figure
 * (.v3d-view with .v3d-canvas and .v3d-wait) and a button bar. The box has the class "loading" while Mol* and the
 * structure are being fetched (the figure stays, with a message and a seconds counter on it), then "on".
 *
 * The structure is the PDB entry the figure was drawn from, fetched by the reader's browser from RCSB
 * (binary CIF); nothing is stored on this site. Colours come from the page: each polymer entity and each
 * ligand gets the colour of its swatch in the figure legend (MolViewSpec), so legend and viewer agree.
 * Mol*'s own interaction does the rest: drag to rotate, wheel to zoom, click a residue to zoom to it
 * with the amino acids around it drawn as sticks. Those sticks take the colour of the chain they belong to
 * (carbon only; N, O, S keep the usual element colours), so ribbon and sticks match.
 * "結合部位へ" calls the same focus function on one ligand copy at a time, and goes to the next on every press.
 */
(function () {
  "use strict";
  var ELEMENT = { N: "#3461D1", O: "#E8303A", S: "#E0B21C", P: "#F08A24", F: "#3FA34D", CL: "#3FA34D",
                  BR: "#9A3B1F", I: "#7A2E99", SE: "#E58A1C", B: "#E6918A" };
  var GROUND = "#F0F1EF";
  var NEUTRAL = "#8A93A0";
  var TIMEOUT = 120;    // seconds before a load is given up
  var loading = null;   // promise for Mol* itself
  var current = null;   // the open viewer: { box, viewer, ms }

  function load(base) {
    if (loading) return loading;
    loading = new Promise(function (resolve, reject) {
      var css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = base + "/vendor/molstar/molstar.css";
      document.head.appendChild(css);
      var js = document.createElement("script");
      js.src = base + "/vendor/molstar/molstar.js";
      js.onload = function () { resolve(window.molstar); };
      js.onerror = function () { loading = null; reject(new Error("molstar")); };
      document.head.appendChild(js);
    });
    return loading;
  }

  function hasWebGL() {
    try {
      var c = document.createElement("canvas");
      return !!(c.getContext("webgl2") || c.getContext("webgl"));
    } catch (e) { return false; }
  }

  function hex(c) { return parseInt(c.replace("#", ""), 16); }

  function spec(url, format, ents, ligs) {
    var kids = [];
    ents.forEach(function (e) {
      kids.push({ kind: "component", params: { selector: { label_entity_id: String(e.id) } }, children: [
        { kind: "representation", params: { type: "cartoon" }, children: [
          { kind: "color", params: { color: e.color } }] }] });
    });
    ligs.forEach(function (l) {
      var colors = [{ kind: "color", params: { color: l.color } }];
      Object.keys(ELEMENT).forEach(function (el) {
        colors.push({ kind: "color", params: { color: ELEMENT[el], selector: { type_symbol: el } } });
      });
      // by entity, not by compound name: a ligand called GLY must not pick up the glycines of the protein
      var selector = (l.entity || []).map(function (id) { return { label_entity_id: String(id) }; });
      if (!selector.length) return;
      kids.push({ kind: "component", params: { selector: selector }, children: [
        { kind: "representation", params: { type: "ball_and_stick" }, children: colors }] });
    });
    return { metadata: { version: "1" }, root: { kind: "root", children: [
      { kind: "canvas", params: { background_color: GROUND } },
      { kind: "download", params: { url: url }, children: [
        { kind: "parse", params: { format: format }, children: [
          { kind: "structure", params: { type: "model" }, children: kids }] }] }] } };
  }

  function structureOf(plugin) {
    var cur = plugin.managers.structure.hierarchy.current.structures;
    return cur.length && cur[0].cell.obj ? cur[0].cell.obj.data : null;
  }

  /* Carbon of the amino acids that Mol* draws as sticks around a clicked residue (or a ligand) in the colour of the
     chain's entity, i.e. the colour of its ribbon; a ligand's own chain gets the ligand's colour. */
  function matchFocusColours(ms, viewer, ents, ligs) {
    var plugin = viewer.plugin, structure = structureOf(plugin);
    if (!structure) return Promise.resolve();
    var byEntity = {};
    ents.forEach(function (e) { byEntity[e.id] = e.color; });
    ligs.forEach(function (l) { (l.entity || []).forEach(function (id) { byEntity[id] = l.color; }); });
    var chains = structure.model.atomicHierarchy.chains, n = chains.label_asym_id.rowCount, seen = {}, list = [];
    for (var i = 0; i < n; i++) {                       // one colour per label_asym_id, in Mol*'s own order
      var asym = chains.label_asym_id.value(i);
      if (seen[asym]) continue;
      seen[asym] = true;
      list.push([hex(byEntity[chains.label_entity_id.value(i)] || NEUTRAL), 1]);
    }
    var theme = { name: "element-symbol", params: { carbonColor: { name: "chain-id", params: {
      asymId: "label", palette: { name: "colors", params: { list: { kind: "set", colors: list } } } } } } };
    var node = null;
    plugin.state.behaviors.tree.transforms.forEach(function (t) {
      if (t.transformer && t.transformer.id === "ms-plugin.create-structure-focus-representation") node = t;
    });
    if (!node) return Promise.resolve();
    return plugin.state.updateBehavior(node.transformer, function (p) {
      p.surroundingsParams.colorTheme = theme;
      p.targetParams.colorTheme = theme;
    });
  }

  /* Every copy of every ligand drawn in the figure, the article's main ligand first. */
  function sitesOf(box) {
    var main = box.dataset.site, out = [];
    JSON.parse(box.dataset.ligs || "[]").forEach(function (l) {
      (l.sites || []).forEach(function (s) { out.push({ comp: l.id, name: l.label, asym: s[0], seq: String(s[1]) }); });
    });
    return out.filter(function (s) { return s.comp === main; }).concat(out.filter(function (s) { return s.comp !== main; }));
  }

  function focusSite(box, step) {
    var S = current.ms.lib.structure, plugin = current.viewer.plugin, P = S.StructureProperties;
    var sites = sitesOf(box);
    if (!sites.length) return;
    var i = (step + sites.length) % sites.length, s = sites[i];
    var structure = structureOf(plugin);
    if (!structure) return;
    var q = S.Queries.generators.atoms({ residueTest: function (ctx) {
      var e = ctx.element;
      return P.residue.label_comp_id(e) === s.comp && P.chain.label_asym_id(e) === s.asym &&
             String(P.residue.auth_seq_id(e)) === s.seq;
    } });
    var loci = S.StructureSelection.toLociWithSourceUnits(q(new S.QueryContext(structure)));
    if (!loci.elements.length) return;
    box.dataset.at = String(i);
    plugin.managers.structure.focus.setFromLoci(loci);
    plugin.managers.camera.focusLoci(loci);
    var now = box.querySelector(".v3d-now");
    if (now) now.textContent = "結合部位 " + (i + 1) + " / " + sites.length + "：" + s.name;
  }

  function say(box, text) {
    var n = box.querySelector(".v3d-msg");
    n.textContent = text;
    n.hidden = !text;
  }

  function stop(box) {
    box._token = (box._token || 0) + 1;                 // anything still loading for this box is now stale
    if (box._timer) { clearInterval(box._timer); box._timer = null; }
    if (current && current.box === box) {
      try { current.viewer.dispose(); } catch (e) { /* already gone */ }
      current = null;
    }
    box.querySelector(".v3d-canvas").innerHTML = "";
    box.classList.remove("on", "loading");
    delete box.dataset.at;
    var now = box.querySelector(".v3d-now");
    if (now) now.textContent = "";
  }

  function open(box) {
    if (current) stop(current.box);
    var pdb = box.dataset.pdb, base = box.dataset.assets;
    var ents = JSON.parse(box.dataset.ents || "[]"), ligs = JSON.parse(box.dataset.ligs || "[]");
    var url = box.dataset.url || "https://models.rcsb.org/" + pdb + ".bcif";
    var format = box.dataset.url ? "mmcif" : "bcif";
    if (!hasWebGL()) { say(box, "この端末では 3D 表示（WebGL）を使えない。RCSB のページで開ける。"); return; }
    say(box, "");
    stop(box);
    var token = box._token, started = Date.now();
    var step = box.querySelector(".v3d-wait .step"), secs = box.querySelector(".v3d-wait .secs");
    function stage(text) { step.textContent = text; }
    function tick() {
      var s = Math.round((Date.now() - started) / 1000);
      secs.textContent = "経過 " + s + " 秒" + (s >= 15 ? "（回線が遅いか、混んでいる。まだ動いている）" : "");
      if (s >= TIMEOUT) fail();
    }
    function fail() {
      if (box._token !== token) return;
      stop(box);
      say(box, "3D 表示を読み込めなかった（RCSB に接続できない、通信が遅すぎる、または古いブラウザ）。RCSB のページで開ける。");
    }
    function alive() { return box._token === token; }
    box.classList.add("loading");
    stage("1/3　3D 表示のプログラム（Mol*、約 5 MB）を読み込み中");
    tick();
    box._timer = setInterval(tick, 1000);
    load(base).then(function (ms) {
      if (!alive()) return null;
      stage("2/3　3D 表示を準備中");
      return ms.Viewer.create(box.querySelector(".v3d-canvas"), {
        layoutIsExpanded: false, layoutShowControls: false, layoutShowRemoteState: false, layoutShowSequence: false,
        layoutShowLog: false, layoutShowLeftPanel: false, viewportShowExpand: false, viewportShowSelectionMode: false,
        viewportShowAnimation: false, viewportBackgroundColor: GROUND
      }).then(function (viewer) {
        if (!alive()) { try { viewer.dispose(); } catch (e) { /* ignore */ } return null; }
        current = { box: box, viewer: viewer, ms: ms };
        try {
          viewer.plugin.canvas3d.setProps({ postprocessing: {
            occlusion: { name: "on", params: { multiScale: { name: "off", params: {} }, radius: 5, bias: 0.8,
              blurKernelSize: 15, blurDepthBias: 0.5, samples: 32, resolutionScale: 1, color: 0x000000,
              transparentThreshold: 0.4 } } } });
        } catch (e) { /* depth shading is a nicety */ }
        stage("3/3　PDB " + pdb + " の座標を RCSB から取得して描画中");
        return viewer.loadMvsData(JSON.stringify(spec(url, format, ents, ligs)), "mvsj").then(function () {
          return matchFocusColours(ms, viewer, ents, ligs).catch(function () { /* default colours are still usable */ });
        });
      });
    }).then(function () {
      if (!alive() || !current) return;
      clearInterval(box._timer);
      box._timer = null;
      box.classList.remove("loading");
      box.classList.add("on");
    }).catch(fail);
  }

  document.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest("[data-v3d]");
    if (!b) return;
    var box = b.closest(".v3d");
    var act = b.dataset.v3d;
    if (act === "open") open(box);
    else if (act === "close") { stop(box); say(box, ""); }
    else if (current && current.box === box && box.classList.contains("on")) {
      if (act === "site") focusSite(box, box.dataset.at === undefined ? 0 : Number(box.dataset.at) + 1);
      else if (act === "reset") {
        current.viewer.plugin.managers.structure.focus.clear();
        current.viewer.plugin.managers.camera.reset();
        delete box.dataset.at;
        var now = box.querySelector(".v3d-now");
        if (now) now.textContent = "";
      }
    }
  });
})();
