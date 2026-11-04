// Fit Plotly charts to small screens and to dark mode. The figures are built for a desktop
// page in light mode; on a phone their side legends and wide margins squeeze the plot area.
(function () {
  if (!window.Plotly) return;
  function fit() {
    var narrow = window.matchMedia("(max-width: 600px)").matches;
    var ink = getComputedStyle(document.body).color;
    document.querySelectorAll(".js-plotly-plot").forEach(function (el) {
      var lay = el.layout || {};
      var upd = { "font.color": ink, "title.font.color": ink };
      if (narrow) {
        upd["margin.l"] = 40; upd["margin.r"] = 8; upd["margin.t"] = 44;
        upd["font.size"] = 11;
        upd["legend.orientation"] = "h"; upd["legend.x"] = 0; upd["legend.y"] = -0.3;
        upd["legend.yanchor"] = "top"; upd["legend.xanchor"] = "left";
        upd["margin.b"] = 120;
        upd["height"] = Math.max(lay.height || 380, 460);
        upd["xaxis.tickangle"] = -45;
      }
      Plotly.relayout(el, upd);
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fit); else fit();
  var mq = window.matchMedia("(prefers-color-scheme: dark)");
  if (mq.addEventListener) mq.addEventListener("change", fit);
})();
