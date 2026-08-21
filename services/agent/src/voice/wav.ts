/**
 * PCM ⇄ WAV.
 *
 * Groq's transcription endpoint takes audio *files* — wav, mp3, flac — not raw
 * PCM, so an utterance leaves the ring buffer as samples and arrives at the
 * STT as a 44-byte header plus those same samples. Orpheus hands audio back
 * the same way. Neither direction re-encodes anything; this is framing, not
 * conversion.
 */

const HEADER_BYTES = 44;

/** Wrap mono 16-bit PCM in a canonical WAV header. */
export function encodeWav(pcm: Int16Array, sampleRate: number): Buffer {
  const dataBytes = pcm.length * 2;
  const buf = Buffer.alloc(HEADER_BYTES + dataBytes);

  buf.write('RIFF', 0, 'ascii');
  buf.writeUInt32LE(36 + dataBytes, 4);
  buf.write('WAVE', 8, 'ascii');
  buf.write('fmt ', 12, 'ascii');
  buf.writeUInt32LE(16, 16); // PCM fmt chunk size
  buf.writeUInt16LE(1, 20); // audio format: PCM
  buf.writeUInt16LE(1, 22); // channels: mono
  buf.writeUInt32LE(sampleRate, 24);
  buf.writeUInt32LE(sampleRate * 2, 28); // byte rate
  buf.writeUInt16LE(2, 32); // block align
  buf.writeUInt16LE(16, 34); // bits per sample
  buf.write('data', 36, 'ascii');
  buf.writeUInt32LE(dataBytes, 40);

  Buffer.from(pcm.buffer, pcm.byteOffset, dataBytes).copy(buf, HEADER_BYTES);
  return buf;
}

/**
 * Pull mono 16-bit samples back out of a WAV.
 *
 * Chunks are walked rather than assumed at offset 44: encoders are entitled to
 * emit `LIST`/`fact` chunks before `data`, and slicing at a fixed offset would
 * turn that into a burst of noise played at a patient.
 */
export function decodeWav(buf: Buffer): { pcm: Int16Array; sampleRate: number } {
  if (buf.length < 12 || buf.toString('ascii', 0, 4) !== 'RIFF') {
    throw new Error('not a RIFF file');
  }
  if (buf.toString('ascii', 8, 12) !== 'WAVE') throw new Error('not a WAVE file');

  let sampleRate = 0;
  let bitsPerSample = 16;
  let channels = 1;
  let offset = 12;

  while (offset + 8 <= buf.length) {
    const id = buf.toString('ascii', offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    const body = offset + 8;

    if (id === 'fmt ') {
      channels = buf.readUInt16LE(body + 2);
      sampleRate = buf.readUInt32LE(body + 4);
      bitsPerSample = buf.readUInt16LE(body + 14);
    } else if (id === 'data') {
      if (bitsPerSample !== 16) {
        throw new Error(`expected 16-bit samples, got ${bitsPerSample}`);
      }
      const end = Math.min(body + size, buf.length);
      const interleaved = new Int16Array((end - body) >> 1);
      for (let i = 0; i < interleaved.length; i++) {
        interleaved[i] = buf.readInt16LE(body + i * 2);
      }
      return { pcm: channels > 1 ? downmix(interleaved, channels) : interleaved, sampleRate };
    }

    // Chunks are word-aligned: an odd size is followed by a pad byte.
    offset = body + size + (size % 2);
  }
  throw new Error('WAV has no data chunk');
}

function downmix(interleaved: Int16Array, channels: number): Int16Array {
  const frames = Math.floor(interleaved.length / channels);
  const mono = new Int16Array(frames);
  for (let i = 0; i < frames; i++) {
    let sum = 0;
    for (let c = 0; c < channels; c++) sum += interleaved[i * channels + c] ?? 0;
    mono[i] = Math.round(sum / channels);
  }
  return mono;
}
