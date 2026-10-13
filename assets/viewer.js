/* 3D viewer for the article pages: Mol* (assets/vendor/molstar), loaded only when the reader asks for it.
 *
 * Markup (pipeline/site.py): .v3d[data-pdb][data-ents][data-ligs][data-site][data-assets] holding
 *   .v3d-open (button), .v3d-fig (the static figure), .v3d-stage (the canvas and its buttons).
 * The structure is the PDB entry the figure was drawn from, fetched by the reader's browser from RCSB
 * (binary CIF); nothing is stored on this site. Colours come from the page: each polymer entity and each
 * ligand gets the colour of its swatch in the figure legend (MolViewSpec), so legend and viewer agree.
 * Mol*'s own interaction does the rest: drag to rotate, wheel to zoom, click a residue to zoom to it
 * with the amino acids around it drawn as sticks. "結合部位へ" calls the same focus function on the ligand.
 */
(function () {
  "use strict";
  var ELEMENT = { N: "#3461D1", O: "#E8303A", S: "#E0B21C", P: "#F08A24", F: "#3FA34D", CL: "#3FA34D",
                  BR: "#9A3B1F", I: "#7A2E99", SE: "#E58A1C", B: "#E6918A" };
  var GROUND = "#F0F1EF";
  var loading = null;   // promise for Mol* itself
  var current = null;   // the open viewer: { box, viewer }

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
      kids.push({ kind: "component", params: { selector: { label_comp_id: l.id } }, children: [
        { kind: "representation", params: { type: "ball_and_stick" }, children: colors }] });
    });
    return { metadata: { version: "1" }, root: { kind: "root", children: [
      { kind: "canvas", params: { background_color: GROUND } },
      { kind: "download", params: { url: url }, children: [
        { kind: "parse", params: { format: format }, children: [
          { kind: "structure", params: { type: "model" }, children: kids }] }] }] } };
  }

  function focusLigand(ms, viewer, comp) {
    var S = ms.lib.structure, plugin = viewer.plugin;
    var cur = plugin.managers.structure.hierarchy.current.structures;
    if (!cur.length || !cur[0].cell.obj) return;
    var structure = cur[0].cell.obj.data;
    var q = S.Queries.generators.atoms({ residueTest: function (ctx) {
      return S.StructureProperties.residue.label_comp_id(ctx.element) === comp; } });
    var loci = S.StructureSelection.toLociWithSourceUnits(q(new S.QueryContext(structure)));
    if (!loci.elements.length) return;
    plugin.managers.structure.focus.setFromLoci(loci);
    plugin.managers.camera.focusLoci(loci);
  }

  function close(box) {
    if (current && current.box === box) {
      try { current.viewer.dispose(); } catch (e) { /* already gone */ }
      current = null;
    }
    box.querySelector(".v3d-canvas").innerHTML = "";
    box.classList.remove("on");
  }

  function say(box, text) {
    var n = box.querySelector(".v3d-msg");
    n.textContent = text;
    n.hidden = !text;
  }

  function open(box) {
    if (current) close(current.box);
    var pdb = box.dataset.pdb, base = box.dataset.assets;
    var ents = JSON.parse(box.dataset.ents || "[]"), ligs = JSON.parse(box.dataset.ligs || "[]");
    var url = box.dataset.url || "https://models.rcsb.org/" + pdb + ".bcif";
    var format = box.dataset.url ? "mmcif" : "bcif";
    if (!hasWebGL()) { say(box, "この端末では 3D 表示（WebGL）を使えない。RCSB のページで開ける。"); return; }
    say(box, "読み込み中…（PDB " + pdb + "）");
    box.classList.add("on");
    load(base).then(function (ms) {
      return ms.Viewer.create(box.querySelector(".v3d-canvas"), {
        layoutIsExpanded: false, layoutShowControls: false, layoutShowRemoteState: false, layoutShowSequence: false,
        layoutShowLog: false, layoutShowLeftPanel: false, viewportShowExpand: false, viewportShowSelectionMode: false,
        viewportShowAnimation: false, viewportBackgroundColor: GROUND
      }).then(function (viewer) {
        current = { box: box, viewer: viewer, ms: ms };
        try {
          viewer.plugin.canvas3d.setProps({ postprocessing: {
            occlusion: { name: "on", params: { multiScale: { name: "off", params: {} }, radius: 5, bias: 0.8,
              blurKernelSize: 15, blurDepthBias: 0.5, samples: 32, resolutionScale: 1, color: 0x000000,
              transparentThreshold: 0.4 } } } });
        } catch (e) { /* depth shading is a nicety */ }
        return viewer.loadMvsData(JSON.stringify(spec(url, format, ents, ligs)), "mvsj");
      });
    }).then(function () { say(box, ""); }).catch(function () {
      close(box);
      say(box, "3D 表示を読み込めなかった（RCSB に接続できない、または古いブラウザ）。RCSB のページで開ける。");
    });
  }

  document.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest("[data-v3d]");
    if (!b) return;
    var box = b.closest(".v3d");
    var act = b.dataset.v3d;
    if (act === "open") open(box);
    else if (act === "close") { close(box); say(box, ""); }
    else if (current && current.box === box) {
      if (act === "site") focusLigand(current.ms, current.viewer, box.dataset.site);
      else if (act === "reset") {
        current.viewer.plugin.managers.structure.focus.clear();
        current.viewer.plugin.managers.camera.reset();
      }
    }
  });
})();
