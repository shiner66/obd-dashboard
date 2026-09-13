/* Stack-safe rendering for Leaflet 1.9.4; original GPS observations stay untouched. */
(function (root) {
  "use strict";
  /** Squared distance to a segment, matching Leaflet's projection and comparisons. */
  function segmentDistance(point, first, last) {
    let x = first.x, y = first.y, dx = last.x - x, dy = last.y - y;
    if (dx !== 0 || dy !== 0) {
      const t = ((point.x - x) * dx + (point.y - y) * dy) / (dx * dx + dy * dy);
      if (t > 1) { x = last.x; y = last.y; }
      else if (t > 0) { x += dx * t; y += dy * t; }
    }
    dx = point.x - x; dy = point.y - y;
    return dx * dx + dy * dy;
  }
  /** Preserve Leaflet's radial + Douglas–Peucker result using an explicit work stack. */
  function simplify(points, tolerance) {
    if (!tolerance || !points.length) return points.slice();
    const squared = tolerance * tolerance, reduced = [points[0]];
    let previous = 0;
    for (let i = 1; i < points.length; i++) {
      const dx = points[i].x - points[previous].x, dy = points[i].y - points[previous].y;
      if (dx * dx + dy * dy > squared) { reduced.push(points[i]); previous = i; }
    }
    if (previous < points.length - 1) reduced.push(points[points.length - 1]);
    const marked = new Uint8Array(reduced.length), pending = [0, reduced.length - 1];
    marked[0] = marked[reduced.length - 1] = 1;
    while (pending.length) {
      const last = pending.pop(), first = pending.pop();
      let maximum = 0, farthest = first;
      for (let i = first + 1; i < last; i++) {
        const distance = segmentDistance(reduced[i], reduced[first], reduced[last]);
        if (distance > maximum) { maximum = distance; farthest = i; }
      }
      if (maximum > squared) {
        marked[farthest] = 1;
        if (farthest - first > 1) pending.push(first, farthest);
        if (last - farthest > 1) pending.push(farthest, last);
      }
    }
    return reduced.filter((point, i) => marked[i]);
  }
  const classes = new WeakMap();
  /** Override only our polyline's simplification, including every zoom/redraw path.
   * Leaflet 1.9.4 calls _simplifyPoints after clipping projected pixel coordinates.
   * Assigning L.LineUtil.simplify would NOT replace its internal lexical function.
   */
  function polyline(leaflet, coordinates, options) {
    if (!classes.has(leaflet)) classes.set(leaflet, leaflet.Polyline.extend({
      _simplifyPoints() {
        this._parts = this._parts.map(points => simplify(points, this.options.smoothFactor));
      },
    }));
    const Line = classes.get(leaflet);
    return new Line(coordinates, options);
  }
  const api = { simplify, polyline };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.OBDMap = api;
})(typeof window !== "undefined" ? window : globalThis);
