import './style.css';
import { App, INDIA, type Plan, type View } from './app';
import { loadData, type LineKey } from './data';
import { StoryPlayer } from './story';
import { el, esc } from './ui';

async function boot(): Promise<void> {
  const status = el('status');
  const params = new URLSearchParams(location.search);
  let data;
  try {
    data = await loadData('./data/', (m) => { status.textContent = m; });
  } catch (e) {
    status.innerHTML = `<b>Could not load the map data.</b><br>${esc((e as Error).message)}`;
    console.error(e);
    return;
  }
  status.textContent = 'Building the map';
  const app = new App(data);
  app.mount(el<HTMLDivElement>('map'));
  status.hidden = true;

  const story = data.story?.beats?.length ? new StoryPlayer(app, data.story) : null;
  el('story-btn').hidden = !story;

  // ------------------------------------------------------- delegated events
  document.addEventListener('click', (ev) => {
    const t = ev.target as HTMLElement;
    const btn = t.closest('button, [data-fac]') as HTMLElement | null;
    if (!btn) return;
    const ds = btn.dataset;
    if (ds.line) app.setLine(ds.line as LineKey);
    else if (ds.view) app.setView(ds.view as View);
    else if (ds.grade) app.setGrade(Number(ds.grade));
    else if (ds.r) app.setRadius(Number(ds.r));
    else if (ds.q) { app.quadMode = ds.q as 'model' | 'rule'; app.recolor(); app.renderUI(); }
    else if (ds.plan) { app.plan = ds.plan as Plan; app.renderUI(); }
    else if (ds.close) {
      if (ds.close === 'fac') app.selectedFac = null;
      if (ds.close === 'district') app.selectedDistrict = null;
      if (ds.close === 'patient') app.setPatient(null);
      app.renderUI();
    } else if (ds.fac !== undefined) {
      const i = Number(ds.fac);
      if (i !== data.noIndex) app.showFacility(i);
    } else if (btn.id === 'play-btn') {
      if (app.playback) app.stopPlayback();
      else app.startPlayback([app.plan], 10, 1.2);
    } else if (btn.id === 'release-btn') { app.releaseSwarm(); app.renderUI(); }
    else if (btn.id === 'reset-btn') app.resetSim();
    else if (btn.id === 'drawer-btn') app.openDrawer(!el('drawer').classList.contains('open'));
    else if (btn.id === 'drawer-close') app.openDrawer(false);
    else if (btn.id === 'story-btn' && story) story.start(false);
    else if (btn.id === 'home-btn') app.flyTo(INDIA, 1.2);
  });
  document.addEventListener('change', (ev) => {
    const t = ev.target as HTMLInputElement;
    if (t.id === 'swarm-toggle') { app.showSwarm = t.checked; app.renderUI(); }
  });
  el<HTMLInputElement>('grade-slider').addEventListener('input', (ev) => {
    app.setGrade(Number((ev.target as HTMLInputElement).value));
  });

  window.addEventListener('keydown', (ev) => {
    if ((ev.target as HTMLElement)?.tagName === 'INPUT' && (ev.target as HTMLInputElement).type !== 'range') return;
    if (ev.code === 'Space') {
      ev.preventDefault();
      (document.activeElement as HTMLElement | null)?.blur();
      if (!story) return;
      if (story.active) story.next();
      else story.start(false);
    } else if (ev.code === 'ArrowLeft' && story?.active) story.prev();
    else if (ev.code === 'Escape') {
      if (story?.active) story.stop();
      app.openDrawer(false);
      app.showOverlay(null);
    } else if (ev.key === 'f') {
      const f = el('fps');
      f.hidden = !f.hidden;
      app.fpsEl = f.hidden ? null : f;
    }
  });

  // --------------------------------------------- hooks for checks and recording
  const w = window as unknown as Record<string, unknown>;
  w.__vf = {
    app,
    story,
    fps: () => app.fps(),
    checkScenarios: () => app.checkAllScenarios(),
    simulateStory: () => story?.simulate() ?? null,
    seekStory: (s: number) => story?.seek(s),
    resume: () => { app.clock.paused = false; },
    project: (lon: number, lat: number) => app.deck.getViewports()[0]?.project([lon, lat]),
  };

  if (story && (params.has('story') || params.has('autoplay'))) story.start(params.has('autoplay'));
}

boot();
