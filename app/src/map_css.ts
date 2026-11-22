// Map styles, injected once by MapHost (the map owns no .css file). Class prefix "vfm-".
// Colours are the SPEC section 1 tokens.

import { BORDER, EVIDENCE, INK, NEUTRAL, ORIGIN, PAPER, SURROUND, WHATIF } from './colors';

const CSS = `
.vfm-inline { position: relative; overflow: hidden; background: ${SURROUND}; border-radius: 6px; min-height: 200px; }
.vfm-stage { position: absolute; inset: 0; overflow: hidden; background: ${SURROUND};
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: ${INK}; touch-action: none; }
.vfm-canvas { position: absolute; inset: 0; }
.vfm-canvas canvas { outline: none; }
.vfm-btn { font: 600 13px/1 system-ui, -apple-system, "Segoe UI", sans-serif; color: ${INK};
  background: rgba(255,255,255,0.94); border: 1px solid rgba(43,38,32,0.25); border-radius: 6px;
  padding: 7px 10px; cursor: pointer; box-shadow: 0 1px 2px rgba(0,0,0,0.12); min-height: 32px; }
.vfm-btn:hover { background: #fff; border-color: rgba(43,38,32,0.5); }
.vfm-btn:focus-visible { outline: 2px solid ${EVIDENCE}; outline-offset: 1px; }
.vfm-zoom { position: absolute; top: 10px; left: 10px; display: flex; flex-direction: column; gap: 4px; z-index: 3; }
.vfm-zoom .vfm-btn { width: 34px; height: 34px; padding: 0; font-size: 19px; }
.vfm-ladder { display: none; flex-direction: column; gap: 3px; margin-top: 6px; }
.vfm-ladder .vfm-btn { width: auto; height: auto; min-height: 26px; font-size: 11.5px; padding: 5px 8px; text-align: left; }
.vfm-ladder .vfm-btn.on { background: ${INK}; color: ${PAPER}; border-color: ${INK}; }
.vfm-expanded .vfm-ladder { display: flex; }
.vfm-expand { position: absolute; top: 10px; right: 10px; z-index: 3; }
.vfm-expanded .vfm-expand { display: none; }
.vfm-inset { position: absolute; left: 10px; bottom: 10px; width: 120px; height: 130px; padding: 0; z-index: 3;
  border: 1px solid rgba(43,38,32,0.35); border-radius: 4px; background: ${SURROUND}; cursor: pointer;
  box-shadow: 0 1px 3px rgba(0,0,0,0.18); overflow: hidden; }
.vfm-small .vfm-inset { width: 78px; height: 85px; }
.vfm-small .vfm-inset::after { display: none; }
.vfm-small .vfm-zoom .vfm-btn { width: 30px; height: 30px; min-height: 30px; font-size: 17px; }
.vfm-inset svg { width: 100%; height: 100%; display: block; }
.vfm-inset::after { content: "India"; position: absolute; left: 5px; top: 3px; font: 600 10px/1 system-ui, sans-serif;
  color: #6b5d4b; letter-spacing: 0.06em; text-transform: uppercase; }
.vfm-scale { position: absolute; right: 10px; bottom: 10px; z-index: 3; display: flex; align-items: flex-end; gap: 10px;
  font: 500 11px/1.1 system-ui, sans-serif; color: ${INK}; pointer-events: none; }
.vfm-scale-bar { display: flex; flex-direction: column; align-items: flex-start; gap: 3px; background: rgba(245,240,230,0.85);
  padding: 4px 6px; border-radius: 3px; }
.vfm-scale-line { height: 5px; border: 1.5px solid ${INK}; border-top: none; }
.vfm-north { background: rgba(245,240,230,0.85); padding: 4px 6px; border-radius: 3px; font-weight: 700; }
.vfm-hint { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); z-index: 4; pointer-events: none;
  background: rgba(43,38,32,0.82); color: #fff; font: 500 13px/1.3 system-ui, sans-serif; padding: 8px 12px;
  border-radius: 6px; opacity: 0; transition: opacity 250ms; }
.vfm-hint.on { opacity: 1; }
.vfm-banner { position: absolute; left: 0; right: 0; top: 0; z-index: 5; }
.vfm-banner:empty { display: none; }
.vfm-banner ~ .vfm-zoom, .vfm-banner ~ .vfm-expand { transition: top 150ms; }
.vfm-stage.has-banner .vfm-zoom, .vfm-stage.has-banner .vfm-expand { top: 52px; }
.map-tip, .vfm-tip { font: 500 12.5px/1.35 system-ui, sans-serif !important; background: ${INK} !important; color: #fff !important;
  padding: 6px 9px !important; border-radius: 5px; max-width: 280px; }

.vfm-legend { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: 4px 14px;
  font: 500 12.5px/1.35 system-ui, -apple-system, "Segoe UI", sans-serif; color: ${INK}; }
.vfm-legend:empty { display: none; }
.vfm-legend li { display: flex; align-items: center; gap: 6px; }
.vfm-sw { flex: none; display: inline-block; width: 18px; height: 18px; position: relative; }
.vfm-sw svg { width: 18px; height: 18px; display: block; }

.vfm-overlay { position: fixed; inset: 0; z-index: 1000; background: ${PAPER}; display: grid;
  grid-template-rows: auto 1fr; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: ${INK}; }
.vfm-top { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-bottom: 1px solid rgba(43,38,32,0.18);
  background: ${PAPER}; flex-wrap: wrap; }
.vfm-top h2 { font: 600 17px/1.2 "Iowan Old Style", Palatino, Georgia, serif; margin: 0; flex: 1 1 auto; }
.vfm-top-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.vfm-top .vfm-close { background: ${INK}; color: ${PAPER}; border-color: ${INK}; }
.vfm-body { display: grid; grid-template-columns: 1fr; min-height: 0; }
.vfm-body.with-rail { grid-template-columns: 1fr 400px; }
.vfm-map { position: relative; min-height: 0; min-width: 0; }
.vfm-rail { overflow: auto; border-left: 1px solid rgba(43,38,32,0.18); background: ${PAPER}; min-height: 0; }
.vfm-rail:empty, .vfm-body:not(.with-rail) .vfm-rail { display: none; }
.vfm-map .vfm-legend-float { position: absolute; left: 142px; bottom: 10px; z-index: 3; max-width: calc(100% - 230px);
  max-height: 38%; overflow: auto;
  background: rgba(245,240,230,0.94); border: 1px solid rgba(43,38,32,0.18); border-radius: 6px; padding: 6px 10px; }
.vfm-map .vfm-legend-float .vfm-legend { margin: 0; }
.vfm-key-btn { display: none; }
.vfm-railkey { order: 5; margin: 0 12px 12px; border: 1px solid rgba(43,38,32,0.18); border-radius: 8px; padding: 6px 10px; background: rgba(255,253,248,0.7); }
.vfm-railkey > summary { font: 700 13px/1.3 system-ui, sans-serif; cursor: pointer; padding: 2px 0; }
.vfm-railkey .vfm-legend { flex-direction: column; gap: 5px; margin-top: 6px; font-size: 12.5px; }
@media (max-width: 700px) {
  .vfm-inline .vfm-inset { width: 90px; height: 100px; }
  .vfm-body, .vfm-body.with-rail { grid-template-columns: 1fr; grid-template-rows: 40vh 1fr; }
  .vfm-body:not(.with-rail) { grid-template-rows: 1fr; }
  .vfm-rail { border-left: none; border-top: 1px solid rgba(43,38,32,0.18); }
  .vfm-map .vfm-legend-float { left: auto; right: 10px; max-width: calc(100% - 120px); bottom: 44px; font-size: 11.5px;
    background: transparent; border: none; padding: 0; text-align: right; }
  .vfm-map .vfm-legend-float .vfm-legend { display: none; text-align: left; margin-top: 6px; background: rgba(245,240,230,0.96);
    border: 1px solid rgba(43,38,32,0.18); border-radius: 6px; padding: 6px 10px; }
  .vfm-map .vfm-legend-float.open .vfm-legend { display: flex; }
  .vfm-key-btn { display: inline-block; }
  .vfm-ladder, .vfm-expanded .vfm-ladder { display: none; }
  .vfm-overlay .vfm-inset { width: 90px; height: 100px; }
  .vfm-top h2 { font-size: 15px; }
  .vfm-top .vfm-btn { padding: 6px 8px; font-size: 12px; }
}
.vfm-chip-whatif { color: ${WHATIF}; }
.vfm-chip-origin { color: ${ORIGIN}; }
.vfm-chip-neutral { color: ${NEUTRAL}; }
.vfm-chip-border { color: ${BORDER}; }
`;

let injected = false;

export function injectMapCss(): void {
  if (injected) return;
  injected = true;
  const s = document.createElement('style');
  s.dataset.vfMap = '1';
  s.textContent = CSS;
  document.head.appendChild(s);
}
