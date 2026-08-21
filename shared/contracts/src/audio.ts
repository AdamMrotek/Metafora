/**
 * The audio format agreed between `svc-media` and `svc-agent`.
 *
 * It sits here rather than in either service because both need it and neither
 * owns it: the edge has to know what frames to hand over, the agent has to
 * know what it is being handed. Silero's frame size is what fixes the numbers
 * — 512 samples at 16 kHz — and everything downstream is derived from that.
 */

/** Silero VAD wants exactly 512 samples at 16 kHz. 512 / 16000 = 32 ms. */
export const SAMPLE_RATE = 16_000;
export const FRAME_SIZE_MS = 32;
export const FRAME_SAMPLES = 512;

/** Output side: Orpheus can hand us 48 kHz directly, so nothing is resampled. */
export const OUTPUT_SAMPLE_RATE = 48_000;
