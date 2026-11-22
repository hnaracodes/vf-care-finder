// Colour tokens (SPEC section 1), one meaning each, from A's colors.ts, with
// the deck.gl RGBA forms the simulator's layers use.

export type RGBA = [number, number, number, number];

import { BORDER, CLAIM, EVIDENCE, INK, NEUTRAL, ORIGIN, PAPER, SURROUND, WHATIF } from '../colors';

export const HEX = { INK, PAPER, SURROUND, EVIDENCE, CLAIM, WHATIF, NEUTRAL, ORIGIN, BORDER } as const;

export function rgba(hex: string, a = 255): RGBA {
  const v = parseInt(hex.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255, a];
}

export const C = {
  ink: rgba(HEX.INK),
  evidence: rgba(HEX.EVIDENCE),
  claim: rgba(HEX.CLAIM),
  whatif: rgba(HEX.WHATIF),
  neutral: rgba(HEX.NEUTRAL),
  origin: rgba(HEX.ORIGIN),
  paper: rgba(HEX.PAPER),
};
