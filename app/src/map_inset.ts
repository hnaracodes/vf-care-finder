// The India locator inset (app/design/SPEC.md 5.3): an SVG of the states drawn once with an
// equirectangular fit, and a red box for the current view (a red dot when the box is tiny).
// Always visible, so the viewer always knows where in India the map is.

import { BORDER, ORIGIN, PAPER, SURROUND } from './colors';
import type { GeoAssets } from './map_assets';

const X0 = 67.6, X1 = 97.8, Y0 = 6.2, Y1 = 37.4;
const NS = 'http://www.w3.org/2000/svg';

export class Inset {
  readonly el: HTMLButtonElement;
  private svg: SVGSVGElement;
  private box: SVGRectElement;
  private dot: SVGCircleElement;
  private W = 120;
  private H = 130;

  constructor(onClick: () => void) {
    this.el = document.createElement('button');
    this.el.type = 'button';
    this.el.className = 'vfm-inset';
    this.el.title = 'Where this is in India. Click to show all of India.';
    this.el.setAttribute('aria-label', 'India locator map. Click to show all of India.');
    this.svg = document.createElementNS(NS, 'svg');
    this.svg.setAttribute('viewBox', `0 0 ${this.W} ${this.H}`);
    this.svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    const bg = document.createElementNS(NS, 'rect');
    bg.setAttribute('width', String(this.W)); bg.setAttribute('height', String(this.H)); bg.setAttribute('fill', SURROUND);
    this.svg.appendChild(bg);
    this.box = document.createElementNS(NS, 'rect');
    this.box.setAttribute('fill', 'rgba(192,57,43,0.12)');
    this.box.setAttribute('stroke', ORIGIN);
    this.box.setAttribute('stroke-width', '1.4');
    this.dot = document.createElementNS(NS, 'circle');
    this.dot.setAttribute('r', '3.2');
    this.dot.setAttribute('fill', ORIGIN);
    this.dot.setAttribute('stroke', '#fff');
    this.dot.setAttribute('stroke-width', '1');
    this.el.appendChild(this.svg);
    this.el.addEventListener('click', (e) => { e.stopPropagation(); onClick(); });
  }

  private x(lon: number): number { return ((lon - X0) / (X1 - X0)) * this.W; }
  private y(lat: number): number { return ((Y1 - lat) / (Y1 - Y0)) * this.H; }

  draw(geo: GeoAssets): void {
    let d = '';
    for (const s of geo.states) {
      for (const poly of s.polygons) {
        for (const r of poly) {
          if (r.length < 4) continue;
          d += 'M' + r.map(([lon, lat]) => `${this.x(lon).toFixed(1)},${this.y(lat).toFixed(1)}`).join('L') + 'Z';
        }
      }
    }
    const p = document.createElementNS(NS, 'path');
    p.setAttribute('d', d);
    p.setAttribute('fill', PAPER);
    p.setAttribute('stroke', BORDER);
    p.setAttribute('stroke-width', '0.35');
    p.setAttribute('stroke-linejoin', 'round');
    p.setAttribute('fill-rule', 'evenodd');
    this.svg.insertBefore(p, this.svg.childNodes[1] ?? null);
    this.svg.appendChild(this.box);
    this.svg.appendChild(this.dot);
  }

  /** bounds = [west, south, east, north]; marker = the searched place, if any. */
  update(bounds: [number, number, number, number], marker: [number, number] | null): void {
    const [w, s, e, n] = bounds;
    const x0 = Math.max(-2, this.x(w)), x1 = Math.min(this.W + 2, this.x(e));
    const y0 = Math.max(-2, this.y(n)), y1 = Math.min(this.H + 2, this.y(s));
    const bw = x1 - x0, bh = y1 - y0;
    const covers = bw >= this.W * 0.9 && bh >= this.H * 0.9;
    if (bw < 8 && bh < 8) {
      this.box.setAttribute('display', 'none');
      const [lon, lat] = marker ?? [(w + e) / 2, (s + n) / 2];
      this.dot.setAttribute('cx', this.x(lon).toFixed(1));
      this.dot.setAttribute('cy', this.y(lat).toFixed(1));
      this.dot.setAttribute('display', '');
    } else {
      this.box.setAttribute('display', covers ? 'none' : '');
      this.box.setAttribute('x', x0.toFixed(1)); this.box.setAttribute('y', y0.toFixed(1));
      this.box.setAttribute('width', Math.max(0, bw).toFixed(1)); this.box.setAttribute('height', Math.max(0, bh).toFixed(1));
      if (marker) {
        this.dot.setAttribute('cx', this.x(marker[0]).toFixed(1));
        this.dot.setAttribute('cy', this.y(marker[1]).toFixed(1));
        this.dot.setAttribute('display', '');
      } else this.dot.setAttribute('display', 'none');
    }
  }
}
