const ns = 'http://www.w3.org/2000/svg';
function mark(svg, tag, attrs, text) {
  const node = document.createElementNS(ns, tag);
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  if (text !== undefined) node.textContent = text;
  svg.append(node);
  return node;
}
const line = (svg, x1, y1, x2, y2, cls = 'line') => mark(svg, 'line', { x1, y1, x2, y2, class: cls });
const rect = (svg, x, y, width, height, cls) => mark(svg, 'rect', { x, y, width, height, class: cls });
const dot = (svg, cx, cy, cls, r = 3) => mark(svg, 'circle', { cx, cy, r, class: cls });
const text = (svg, x, y, value) => mark(svg, 'text', { x, y }, value);
const path = (svg, points, cls) => mark(svg, 'polyline', { points: points.map((p) => p.join(',')).join(' '), class: cls });
function scale(lo, hi, a, b) { return (n) => a + (b - a) * (n - lo) / (hi - lo || 1); }
function range(values, fallback = [0, 1]) { return values.length ? [Math.min(...values), Math.max(...values)] : fallback; }
function grid(svg, a, b, unit) {
  for (let index = 0; index <= 5; index++) {
    const x = 58 + index * 122;
    line(svg, x, 30, x, 197);
    text(svg, x - 7, 217, Number((a + (b - a) * index / 5).toPrecision(4)));
  }
  text(svg, 686, 217, unit);
}

function render(svg, { outputs, source }) {
    const points = outputs.level;
    const [a,b] = range([...points.map((p) => p.time), ...outputs.gate.map((p) => p.time)]);
    const [lo,hi] = range(points.map((p) => p.level));
    const x = scale(a,b,58,668), y = scale(lo,hi,185,40);
    grid(svg,a,b,'秒');
    for (const gate of outputs.gate) line(svg, x(gate.time), 30, x(gate.time), 192, 'line');
    for (const [i,key] of ['chamber','lower','upper'].entries()) {
      const group = points.filter((p) => p.station === key);
      const cls = ['series','series secondary','series alert'][i];
      for (const [n,p] of group.entries()) {
        dot(svg, x(p.time), y(p.level), i === 2 ? 'warning' : 'mark');
        if (n && p.time - group[n-1].time <= source.max_gap) line(svg,x(group[n-1].time),y(group[n-1].level),x(p.time),y(p.level),cls);
      }
    }
    text(svg,58,24,`高程 ${lo} 至 ${hi} 米`);
}

export function draw(svg, packet, config) {
  svg.replaceChildren();
  mark(svg, 'title', {}, config.plot);
  render(svg, packet);
}
