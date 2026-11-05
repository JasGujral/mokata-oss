// mokata ide - the pixel difference of two images (0.0.27 P9, ID-IMAGE-DIFF). Runs in a Worker so a
// large image never blocks the page; the page can stop it at any moment by terminating it.
//   in:  { a: ImageBitmap, b: ImageBitmap, tol: 0..255 }   (transferred; a = before, b = after)
//   out: { type: "progress", done, total, differing }  rows done of rows total, once per band
//        { type: "done", w, h, total, differing, size_differs, bitmap }  the picture, transferred
//        { type: "error", note }
// A pixel differs when any channel (alpha included) is further apart than `tol`; a pixel outside
// the smaller image differs by definition. The picture shows differing pixels in red and the rest
// as a faint grey of the AFTER image. The work goes band by band (about a million pixels), so memory
// is the two bitmaps + one result canvas + one band - never a second full copy of either image.
// No DOM is touched here (a Worker has none) and nothing is fetched.
"use strict";

self.onmessage = (ev) => {
  const m = ev.data || {};
  try {
    run(m.a, m.b, m.tol | 0);
  } catch (e) {
    self.postMessage({ type: "error", note: String((e && e.message) || e) });
  }
};

function run(a, b, tol) {
  const W = Math.max(a.width, b.width), H = Math.max(a.height, b.height);
  const band = Math.max(1, Math.min(H, Math.floor(1000000 / W)));
  const out = new OffscreenCanvas(W, H), octx = out.getContext("2d");
  const ca = new OffscreenCanvas(W, band), cb = new OffscreenCanvas(W, band);
  const xa = ca.getContext("2d", { willReadFrequently: true });
  const xb = cb.getContext("2d", { willReadFrequently: true });
  let differing = 0;
  for (let y0 = 0; y0 < H; y0 += band) {
    const h = Math.min(band, H - y0);
    xa.clearRect(0, 0, W, band); xb.clearRect(0, 0, W, band);
    xa.drawImage(a, 0, -y0); xb.drawImage(b, 0, -y0);
    const da = xa.getImageData(0, 0, W, h).data, db = xb.getImageData(0, 0, W, h).data;
    const pic = octx.createImageData(W, h), o = pic.data;
    for (let y = 0; y < h; y++) {
      const gy = y0 + y, rowA = gy < a.height, rowB = gy < b.height;
      for (let x = 0; x < W; x++) {
        const i = (y * W + x) * 4;
        let d;
        if (rowA && rowB && x < a.width && x < b.width) {
          d = Math.abs(da[i] - db[i]) > tol || Math.abs(da[i + 1] - db[i + 1]) > tol ||
              Math.abs(da[i + 2] - db[i + 2]) > tol || Math.abs(da[i + 3] - db[i + 3]) > tol;
        } else d = true;
        if (d) { o[i] = 255; o[i + 1] = 64; o[i + 2] = 64; o[i + 3] = 255; differing++; }
        else {
          const l = ((db[i] * 3 + db[i + 1] * 6 + db[i + 2]) / 10) | 0;
          o[i] = o[i + 1] = o[i + 2] = l; o[i + 3] = db[i + 3] < 90 ? db[i + 3] : 90;
        }
      }
    }
    octx.putImageData(pic, 0, y0);
    self.postMessage({ type: "progress", done: y0 + h, total: H, differing });
  }
  const bitmap = out.transferToImageBitmap();
  self.postMessage({ type: "done", w: W, h: H, total: W * H, differing,
                     size_differs: a.width !== b.width || a.height !== b.height, bitmap }, [bitmap]);
}
