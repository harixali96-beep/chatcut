"""Draggable timeline widget for the chatcut Gradio app.

Design notes, so the next edit does not reintroduce the bugs:

1. Gradio inserts gr.HTML via innerHTML, which never runs <script>. The widget is
   therefore mounted from a `js=` handler that runs in page context.
2. The track is exactly ONE full-second scale. Clips are absolutely positioned by
   percentage of the total output duration, so the picture matches the numbers.
3. During a drag nothing is rebuilt. Earlier versions called draw() on every
   mousemove, which destroyed the node under the cursor and killed the drag after
   a couple of pixels. Only inline styles and label text are touched while
   dragging; the DOM is rebuilt once, on mouseup.
4. The trim and reorder maths live in pure functions so they can be unit tested
   outside a browser.
"""

TL_MOUNT_JS = r"""
(jsonText) => {
  if (!window.__tl) {
    window.__tl = (function () {
      const MIN_LEN = 0.2;

      // ---- pure maths, unit tested -------------------------------
      function trim(seg, mode, dxSec, sourceDuration) {
        const s = { start: seg.start, end: seg.end };
        if (mode === "left") {
          s.start = Math.max(0, Math.min(s.end - MIN_LEN, seg.start + dxSec));
        } else if (mode === "right") {
          s.end = Math.max(s.start + MIN_LEN,
                   Math.min(sourceDuration, seg.end + dxSec));
        }
        return s;
      }

      function targetIndex(fromIndex, dxPx, perClipPx, count) {
        if (!perClipPx) return fromIndex;
        const shift = Math.round(dxPx / perClipPx);
        return Math.max(0, Math.min(count - 1, fromIndex + shift));
      }

      function totalOf(segs) {
        return segs.reduce((a, q) => a + Math.max(0, q.end - q.start), 0);
      }

      // ---- plumbing ---------------------------------------------
      function push(data) {
        const el = document.querySelector("#tl_json textarea")
                || document.querySelector("#tl_json");
        if (!el) return false;
        el.value = JSON.stringify(data);
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      }

      function fmt(t) {
        const m = Math.floor(t / 60), s = Math.floor(t % 60);
        return m + ":" + String(s).padStart(2, "0");
      }

      function mount(jsonText, root) {
        if (!root) return;
        let data;
        try { data = JSON.parse(jsonText); } catch (e) { data = null; }
        if (!data || !data.segments || !data.segments.length) {
          root.innerHTML = '<div class="tl-empty">Upload a video on the Edit tab, ' +
                           'then press "Load timeline".</div>';
          return;
        }
        const srcDur = Math.max(0.1, +data.duration || 1);
        let segs = data.segments.map(s => ({ start: +s.start, end: +s.end }));

        root.innerHTML = "";
        const wrap = document.createElement("div");
        wrap.className = "tl-wrap";
        wrap.innerHTML =
          '<div class="tl-head"><span class="tl-title">Timeline</span>' +
          '<span class="tl-meta"></span></div>' +
          '<div class="tl-ruler"></div>' +
          '<div class="tl-track"></div>' +
          '<div class="tl-hint">Drag a clip sideways to reorder. Drag its edges to ' +
          'trim. Then press "Render timeline".</div>';
        root.appendChild(wrap);

        const track = wrap.querySelector(".tl-track");
        const ruler = wrap.querySelector(".tl-ruler");
        const meta = wrap.querySelector(".tl-meta");
        const nodes = [];

        function layout() {
          const total = Math.max(0.01, totalOf(segs));
          let acc = 0;
          segs.forEach((s, i) => {
            const len = Math.max(0, s.end - s.start);
            const el = nodes[i];
            el.style.left = (acc / total * 100) + "%";
            el.style.width = (len / total * 100) + "%";
            el.querySelector(".tl-label").innerHTML =
              "<b>" + (i + 1) + "</b><small>" + len.toFixed(1) + "s</small>";
            el.title = s.start.toFixed(2) + "s \u2192 " + s.end.toFixed(2) + "s";
            acc += len;
          });
          ruler.innerHTML = "";
          for (let i = 0; i <= 4; i++) {
            const tick = document.createElement("span");
            tick.className = "tl-tick";
            tick.style.left = (i * 25) + "%";
            tick.textContent = fmt(total * i / 4);
            ruler.appendChild(tick);
          }
          meta.textContent = segs.length + " clips \u00b7 " + fmt(total) +
                             " out of " + fmt(srcDur);
          push({ duration: srcDur, segments: segs });
        }

        function draw() {
          track.innerHTML = "";
          nodes.length = 0;
          segs.forEach((s, i) => {
            const el = document.createElement("div");
            el.className = "tl-clip";
            el.style.background = i % 2 ? "#4f46e5" : "#6366f1";
            el.innerHTML = '<div class="tl-handle tl-left"></div>' +
                           '<div class="tl-label"></div>' +
                           '<div class="tl-handle tl-right"></div>';
            track.appendChild(el);
            nodes.push(el);
            bind(el, i);
          });
          layout();
        }

        function bind(el, index) {
          let mode = null, x0 = 0, snap = null, moved = false;

          // onMove and onUp are declared first: start() attaches them, and a
          // const declared afterwards would still be in its dead zone.
          const onMove = (ev) => {
            if (!mode || !snap) return;
            const dx = ev.clientX - x0;
            if (Math.abs(dx) > 1) moved = true;
            if (mode === "move") {
              el.style.transform = "translateX(" + dx + "px)";
              el.style.opacity = "0.75";
              return;
            }
            const total = Math.max(0.01, totalOf(segs));
            const pxPerSec = track.clientWidth / total;
            const next = trim({ start: snap.start, end: snap.end }, mode,
                              dx / Math.max(1, pxPerSec), srcDur);
            segs[index].start = next.start;
            segs[index].end = next.end;
            layout();
          };

          const onUp = (ev) => {
            if (!mode) return;
            document.removeEventListener("mousemove", onMove);
            const dx = ev.clientX - x0;
            const wasMove = mode === "move";
            mode = null;
            if (wasMove) {
              el.style.transform = "";
              el.style.opacity = "";
              if (moved) {
                const perClip = track.clientWidth / Math.max(1, segs.length);
                const to = targetIndex(index, dx, perClip, segs.length);
                snap = null;
                if (to !== index) {
                  const item = segs.splice(index, 1)[0];
                  segs.splice(to, 0, item);
                  draw();
                  return;
                }
              }
            }
            snap = null;
            layout();
          };

          // One mousedown entry point per node. stopPropagation here is safe
          // because the document listeners are attached in this same handler;
          // the earlier version attached them from a second listener that
          // stopPropagation then prevented from ever firing.
          const start = (ev, m) => {
            if (ev.button !== undefined && ev.button !== 0) return;
            mode = m;
            x0 = ev.clientX;
            moved = false;
            snap = { start: segs[index].start, end: segs[index].end };
            document.addEventListener("mousemove", onMove);
            document.addEventListener("mouseup", onUp, { once: true });
            ev.preventDefault();
            ev.stopPropagation();
          };

          el.querySelector(".tl-left").addEventListener("mousedown", e => start(e, "left"));
          el.querySelector(".tl-right").addEventListener("mousedown", e => start(e, "right"));
          el.addEventListener("mousedown", e => start(e, "move"));
        }

        draw();
        window.__tlData = () => ({ duration: srcDur, segments: segs });
        window.__tlReset = (t) => mount(t, root);
      }

      return { mount, push, trim, targetIndex, totalOf };
    })();
  }
  const root = document.querySelector("#tl-root");
  window.__tl.mount(jsonText, root);
}
"""

TL_CSS = """
#tl-root { font-family: ui-sans-serif, system-ui, sans-serif; }
.tl-wrap { border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px;
           background: #fff; }
.tl-head { display: flex; justify-content: space-between; align-items: baseline;
           margin-bottom: 6px; }
.tl-title { font-weight: 600; font-size: 13px; color: #111827; }
.tl-meta { font-size: 11px; color: #6b7280; }
.tl-ruler { position: relative; height: 16px; margin-bottom: 2px;
            border-bottom: 1px solid #e5e7eb; }
.tl-tick { position: absolute; top: 0; font-size: 9px; color: #9ca3af;
           transform: translateX(-50%); }
.tl-track { position: relative; height: 64px; margin-top: 6px; }
.tl-clip { position: absolute; top: 0; bottom: 0; border-radius: 6px; color: #fff;
           cursor: grab; user-select: none; overflow: hidden;
           display: flex; align-items: center; justify-content: center;
           min-width: 18px; transition: opacity .1s; }
.tl-clip:active { cursor: grabbing; }
.tl-label { font-size: 11px; text-align: center; line-height: 1.1;
            pointer-events: none; }
.tl-label small { display: block; opacity: .85; font-size: 9px; }
.tl-handle { position: absolute; top: 0; bottom: 0; width: 10px;
             background: rgba(0,0,0,.22); cursor: ew-resize; }
.tl-left { left: 0; } .tl-right { right: 0; }
.tl-handle:hover { background: rgba(0,0,0,.45); }
.tl-hint { margin-top: 8px; font-size: 10px; color: #9ca3af; }
.tl-empty { padding: 22px; text-align: center; font-size: 12px; color: #9ca3af;
            border: 1px dashed #d1d5db; border-radius: 10px; }
"""
