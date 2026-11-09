// Guided controller run-through. Samples the Gamepad API every few milliseconds, asks the
// user to press each input in turn, and records what changed, how fast the data updates
// and how long quick taps last. Python adds versions, Contanki's view and the hidden
// web view's timing when the report is saved.

const INPUTS = [
   "D-pad Up", "D-pad Down", "D-pad Left", "D-pad Right", "D-pad Up and Right together",
   "A", "B", "X", "Y", "L (left shoulder)", "R (right shoulder)", "L2", "R2",
   "Minus / Select", "Plus / Start", "Home", "Star / Heart (if it has one)",
];
const REST_MS = 3000;
const STEP_TIMEOUT_MS = 20000;
const TAP_COUNT = 10;
const AXIS_ACTIVE = 0.3;

const report = {
   tool_version: window.DIAG_TOOL_VERSION,
   created: new Date().toISOString(),
   user_agent: navigator.userAgent,
   pads: [],
   chosen_pad: null,
   rest: null,
   inputs: [],
   taps: [],
   sampling: null,
   bridge_round_trip_ms: [],
   notes: "",
};

let padIndex = null;
let rest = null;
let onSample = null;
let onSkip = null;
let lastStamp = null;
const loopIntervals = [];
const stampIntervals = [];
let lastLoop = null;
let lastStampAt = null;
let lastDraw = 0;

const app = document.getElementById("app");
app.innerHTML = `
   <h1>Controller diagnostics</h1>
   <p class="muted">Anki ${DIAG_ENV.anki}, Qt ${DIAG_ENV.qt}, Chromium ${DIAG_ENV.chromium || "unknown"},
   ${DIAG_ENV.macos ? "macOS " + DIAG_ENV.macos : DIAG_ENV.platform}</p>
   <div id="step" class="step"></div>
   <div class="controls">
      <button id="skip" hidden>Skip this one</button>
   </div>
   <div id="identified" class="muted"></div>
   <h2>Live input</h2>
   <div id="live" class="live muted">Waiting for a controller.</div>
`;
const stepBox = document.getElementById("step");
const skipButton = document.getElementById("skip");
skipButton.addEventListener("click", () => onSkip && onSkip());

function say(html, canSkip = false) {
   stepBox.innerHTML = html;
   skipButton.hidden = !canSkip;
}

function pads() {
   return Array.from(navigator.getGamepads()).filter((p) => p);
}

function readPad() {
   const pad = navigator.getGamepads()[padIndex];
   if (!pad) return null;
   return {
      t: performance.now(),
      stamp: pad.timestamp,
      buttons: pad.buttons.map((b) => (b.pressed ? Math.max(b.value, 1e-3) : b.value)),
      pressed: pad.buttons.map((b) => b.pressed),
      axes: Array.from(pad.axes),
   };
}

// Which inputs differ from the resting state, and by how much.
function deviation(sample) {
   const buttons = [];
   const axes = [];
   let score = 0;
   sample.pressed.forEach((pressed, i) => {
      const held = pressed || sample.buttons[i] > 0.5;
      if (held !== rest.held[i]) {
         buttons.push({ index: i, value: round(sample.buttons[i]), pressed });
         score += 1;
      }
   });
   sample.axes.forEach((value, i) => {
      const diff = Math.abs(value - rest.axes[i].mean);
      if (diff > AXIS_ACTIVE) {
         axes.push({ index: i, rest: rest.axes[i].mean, value: round(value) });
         score += diff;
      }
   });
   return { active: score > 0, score, buttons, axes, raw_axes: sample.axes.map(round) };
}

function round(v) {
   return Math.round(v * 10000) / 10000;
}

function median(values) {
   if (!values.length) return null;
   const sorted = [...values].sort((a, b) => a - b);
   return round(sorted[Math.floor(sorted.length / 2)]);
}

function tick() {
   const now = performance.now();
   if (lastLoop !== null && loopIntervals.length < 20000) loopIntervals.push(now - lastLoop);
   lastLoop = now;
   if (padIndex === null) {
      const found = pads();
      if (found.length) choosePad(found);
      return;
   }
   const sample = readPad();
   if (!sample) return;
   if (sample.stamp !== lastStamp) {
      if (lastStampAt !== null && stampIntervals.length < 20000) {
         stampIntervals.push(now - lastStampAt);
      }
      lastStamp = sample.stamp;
      lastStampAt = now;
   }
   if (onSample) onSample(sample);
   if (now - lastDraw > 50) {
      draw(sample);
      lastDraw = now;
   }
}

function draw(sample) {
   const pad = navigator.getGamepads()[padIndex];
   const cells = (values, kind) =>
      values
         .map((v, i) => {
            const active =
               kind === "button"
                  ? sample.pressed[i] || v > 0.5
                  : rest && Math.abs(v - rest.axes[i].mean) > AXIS_ACTIVE;
            return `<span class="cell${active ? " on" : ""}">${i}: ${v.toFixed(3)}</span>`;
         })
         .join("");
   document.getElementById("live").innerHTML = `
      <div><b>${escapeHtml(pad.id)}</b> (index ${pad.index}, mapping "${pad.mapping}")</div>
      <div class="row"><span class="label">Buttons</span>${cells(sample.buttons, "button")}</div>
      <div class="row"><span class="label">Axes</span>${cells(sample.axes, "axis")}</div>`;
}

function escapeHtml(text) {
   return text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function choosePad(found) {
   report.pads = found.map((p) => ({
      index: p.index,
      id: p.id,
      mapping: p.mapping,
      buttons: p.buttons.length,
      axes: p.axes.length,
   }));
   // Some controllers (the 8BitDo Micro among them) appear twice; prefer the one that is
   // not the "STANDARD GAMEPAD" duplicate, as Contanki does.
   const preferred = found.find((p) => !/STANDARD GAMEPAD/.test(p.id)) || found[0];
   padIndex = preferred.index;
   report.chosen_pad = padIndex;
   pycmd("diag:identify:" + JSON.stringify(report.pads));
   run();
}

window.diagIdentified = (results) => {
   const lines = results.map((r) =>
      r.pad
         ? `${r.package} ${r.version || ""}: "${escapeHtml(r.pad)}" → ${escapeHtml(JSON.stringify(r.identified_as))}`
         : `Running Contanki: connected ${r.connected}, profile ${escapeHtml(String(r.profile))}`
   );
   document.getElementById("identified").innerHTML = lines.length
      ? "<b>Contanki's view:</b><br>" + lines.join("<br>")
      : "Contanki is not loaded.";
};

function wait(ms) {
   return new Promise((resolve) => setTimeout(resolve, ms));
}

async function measureRest() {
   say(`<b>Step 1.</b> Put the controller down and don't touch it for ${REST_MS / 1000} seconds.`);
   await wait(1500);
   const samples = [];
   onSample = (s) => samples.push(s);
   await wait(REST_MS);
   onSample = null;
   const axisCount = samples[0].axes.length;
   const axes = [];
   for (let i = 0; i < axisCount; i++) {
      const values = samples.map((s) => s.axes[i]);
      const mean = values.reduce((a, b) => a + b, 0) / values.length;
      axes.push({ min: round(Math.min(...values)), max: round(Math.max(...values)), mean: round(mean) });
   }
   const last = samples[samples.length - 1];
   rest = {
      axes,
      held: last.pressed.map((p, i) => p || last.buttons[i] > 0.5),
   };
   report.rest = {
      duration_ms: REST_MS,
      samples: samples.length,
      axes,
      buttons_held_at_rest: rest.held.flatMap((h, i) => (h ? [i] : [])),
      // Chromium reports a released hat switch as 9/7; ordinary axes stay within -1 to 1.
      hat_like_axes: axes.flatMap((a, i) => (Math.abs(a.mean) > 1.05 ? [i] : [])),
      noisy_axes: axes.flatMap((a, i) => (a.max - a.min > 0.02 ? [i] : [])),
   };
}

function inputStep(label, number) {
   return new Promise((resolve) => {
      say(`<b>Step 2 (${number} of ${INPUTS.length}).</b> Press and hold <b>${label}</b> for
         about a second, then let go. Skip it if the controller doesn't have it.`, true);
      let start = null;
      let best = null;
      let restSince = null;
      const timer = setTimeout(() => finish(start ? "timeout_while_held" : "timeout"), STEP_TIMEOUT_MS);
      onSample = (s) => {
         const dev = deviation(s);
         if (start === null) {
            if (dev.active) start = s.t;
            else return;
         }
         if (!best || dev.score > best.score) best = dev;
         if (dev.active) {
            restSince = null;
         } else {
            restSince = restSince ?? s.t;
            if (s.t - restSince > 300) finish("ok");
         }
      };
      onSkip = () => finish("skipped");
      function finish(status) {
         clearTimeout(timer);
         onSample = null;
         onSkip = null;
         resolve({
            label,
            status,
            hold_ms: start !== null && restSince !== null ? Math.round(restSince - start) : null,
            changes: best && { buttons: best.buttons, axes: best.axes, raw_axes: best.raw_axes },
         });
      }
   });
}

function tapStep(label, number) {
   return new Promise((resolve) => {
      say(`<b>Step 3 (${number} of 2).</b> Tap <b>${label}</b> ${TAP_COUNT} times, as quickly
         and lightly as you would when studying.`, true);
      const taps = [];
      let downAt = null;
      let lastUp = null;
      const timer = setTimeout(() => finish("timeout"), STEP_TIMEOUT_MS);
      onSample = (s) => {
         const active = deviation(s).active;
         if (active && downAt === null) {
            downAt = s.t;
         } else if (!active && downAt !== null) {
            taps.push({
               down_ms: Math.round((s.t - downAt) * 10) / 10,
               gap_before_ms: lastUp === null ? null : Math.round(downAt - lastUp),
            });
            lastUp = s.t;
            downAt = null;
            if (taps.length >= TAP_COUNT) finish("ok");
         }
      };
      onSkip = () => finish("skipped");
      function finish(status) {
         clearTimeout(timer);
         onSample = null;
         onSkip = null;
         const durations = taps.map((t) => t.down_ms);
         resolve({
            label,
            status,
            taps,
            down_ms_median: median(durations),
            down_ms_min: durations.length ? Math.min(...durations) : null,
            // Chance that a poll every 50 ms, at a random phase, never sees the tap.
            expected_missed_at_50ms_poll: round(
               durations.reduce((sum, d) => sum + Math.max(0, 1 - d / 50), 0)
            ),
         });
      }
   });
}

async function measureBridge() {
   say("<b>Step 4.</b> Measuring the link between the page and Anki. Nothing to do.");
   for (let i = 0; i < 20; i++) {
      const sent = performance.now();
      const reply = new Promise((resolve) => (window.diagPong = () => resolve()));
      pycmd("diag:ping:" + i);
      await reply;
      report.bridge_round_trip_ms.push(round(performance.now() - sent));
      await wait(50);
   }
}

async function run() {
   await measureRest();
   for (let i = 0; i < INPUTS.length; i++) {
      report.inputs.push(await inputStep(INPUTS[i], i + 1));
   }
   report.taps.push(await tapStep("A", 1));
   report.taps.push(await tapStep("D-pad Right", 2));
   await measureBridge();
   report.sampling = {
      loop_interval_ms_median: median(loopIntervals),
      pad_update_interval_ms_median: median(stampIntervals),
      pad_update_interval_ms_p95: stampIntervals.length
         ? round([...stampIntervals].sort((a, b) => a - b)[Math.floor(stampIntervals.length * 0.95)])
         : null,
      pad_updates_recorded: stampIntervals.length,
   };
   const notes = await askNotes();
   report.notes = notes;
   say("Saving the report…");
   pycmd("diag:save:" + JSON.stringify(report));
}

function askNotes() {
   return new Promise((resolve) => {
      say(`<b>Step 5.</b> Anything to add? For example which mode switch position the
         controller is in, or what goes wrong in Anki.<br>
         <textarea id="notes" rows="4"></textarea><br>
         <button id="finish">Save report</button>`);
      document.getElementById("finish").addEventListener("click", () =>
         resolve(document.getElementById("notes").value)
      );
   });
}

window.diagSaved = (path, hidden) => {
   const skipped = report.inputs.filter((i) => i.status !== "ok").length;
   say(`<b>Done.</b> Saved to <code>${escapeHtml(path)}</code>.<br>
      ${report.inputs.length - skipped} of ${report.inputs.length} inputs recorded;
      gamepad data updated every ${report.sampling.pad_update_interval_ms_median} ms (median);
      Contanki-style hidden poller ran every ${hidden.interval_ms_median} ms (median,
      worst ${hidden.interval_ms_max} ms), page visibility ${hidden.visibility_states.join(", ")}.<br>
      To test another mode, close this window, switch the controller's mode, reconnect it
      and open Tools → Controller Diagnostics again.`);
   window.diagDone = true;
};

window.addEventListener("gamepadconnected", () => {
   if (padIndex === null) say("Controller found.");
});
say("Press any button on the controller so Anki can see it.");
setInterval(tick, 4);
