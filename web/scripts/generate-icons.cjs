/*
 * Placeholder PWA icon generator.
 *
 * Emits minimal, valid solid-colour PNGs for the manifest so the app is
 * installable during development. FINAL ARTWORK IS A DESIGN TASK — these are
 * intentionally simple brand-colour squares, not the shipping icons.
 *
 * Run: `node scripts/generate-icons.cjs`
 */
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function makeCrcTable() {
  const table = new Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
}
const CRC_TABLE = makeCrcTable();

function crc32(buf) {
  let crc = 0xffffffff;
  for (let i = 0; i < buf.length; i++) {
    crc = CRC_TABLE[(crc ^ buf[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const typeBuf = Buffer.from(type, 'ascii');
  const crcBuf = Buffer.alloc(4);
  crcBuf.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])), 0);
  return Buffer.concat([length, typeBuf, data, crcBuf]);
}

/** Build a solid RGB PNG of the given size and colour. */
function makePng(size, [r, g, b]) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0); // width
  ihdr.writeUInt32BE(size, 4); // height
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // colour type: truecolour (RGB)
  ihdr[10] = 0; // compression
  ihdr[11] = 0; // filter
  ihdr[12] = 0; // interlace

  const rowLen = size * 3 + 1; // +1 filter byte per scanline
  const raw = Buffer.alloc(rowLen * size);
  for (let y = 0; y < size; y++) {
    raw[y * rowLen] = 0; // filter type 0 (None)
    for (let x = 0; x < size; x++) {
      const o = y * rowLen + 1 + x * 3;
      raw[o] = r;
      raw[o + 1] = g;
      raw[o + 2] = b;
    }
  }
  const idat = zlib.deflateSync(raw, { level: 9 });
  return Buffer.concat([
    SIGNATURE,
    chunk('IHDR', ihdr),
    chunk('IDAT', idat),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

const outDir = path.join(__dirname, '..', 'public', 'icons');
fs.mkdirSync(outDir, { recursive: true });

// Brand colour #0b0b0f; maskable uses a subtly lighter fill so the safe zone
// is distinguishable from the transparent-free background at a glance.
const BRAND = [0x0b, 0x0b, 0x0f];
const MASKABLE = [0x1a, 0x1a, 0x24];

const targets = [
  { file: 'icon-192.png', size: 192, colour: BRAND },
  { file: 'icon-512.png', size: 512, colour: BRAND },
  { file: 'icon-maskable-512.png', size: 512, colour: MASKABLE },
];

for (const t of targets) {
  const png = makePng(t.size, t.colour);
  fs.writeFileSync(path.join(outDir, t.file), png);
  console.log(`wrote ${t.file} (${t.size}x${t.size}, ${png.length} bytes)`);
}
