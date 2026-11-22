// The map (app/design/SPEC.md section 5). This file is the stable import point for A and D:
//
//   import { MapHost } from './minimap';
//   const map = new MapHost(document.getElementById('map')!);
//
// The implementation lives in map_host.ts (with map_assets, map_labels, map_inset, map_icons,
// map_css, map_text, map_types). The pre-v3 MiniMap and its trip playback are retired: the
// simulator draws through map.setOverlayLayers().

export { MapHost, MapHost as MiniMap, MIN_ZOOM, MAX_ZOOM } from './map_host';
export type {
  Bounds, ExpandedSlots, LegendRow, MapAnswer, MapCandidate, MapExample, MapSim, PickKind, ViewState,
} from './map_host';
export { ICON_MAPPING, iconAtlas } from './map_icons';
export type { IconName } from './map_icons';
