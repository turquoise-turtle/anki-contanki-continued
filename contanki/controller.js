let polling, poll_start, connected_index, indices, ready, mock_index;
let last_report = null;
let last_report_at = 0;

// Checked often enough to catch quick taps (Chromium samples gamepads about every 16 ms),
// but Python only hears about changes, plus a full report once a second to recover from
// anything missed.
const POLL_MS = 16;
const HEARTBEAT_MS = 1000;
// Larger than the jitter of a resting stick, smaller than any deliberate movement.
const AXIS_CHANGE = 0.02;
initialise();

function initialise() {
   if (ready) {
      return;
   }
   try {
      bridgeCommand("contanki::initialise::arg");
      // Causes a lot of console spam if we try to call bridgeCommand too early
   } catch (err) {
      setTimeout(initialise, 1000);
      return;
   }
   window.addEventListener("gamepadconnected", on_controller_connect);
   window.addEventListener("gamepaddisconnected", on_controller_disconnect);
   ready = true;
   mock = false;
}

function on_controller_connect() {
   let controllers = window.navigator.getGamepads();
   let register = "contanki::register";
   indices = [];
   for (let i = 0; i < controllers.length; i++) {
      let con = controllers[i];
      if (con != null && con.connected) {
         indices.push(i);
         register += `::${con.id}%%%${con.buttons.length}%%%${con.axes.length}`;
      }
   }
   if (indices.length == 0) {
      bridgeCommand(
         "contanki::message::No controllers detected. Please reconnect your controller and try again."
      );
   } else if (indices.length > 1) {
      bridgeCommand(register);
   } else {
      connect_controller(indices[0]);
   }
}

function stop_polling() {
   // The poll starts after a delay, so a pending start must be cancelled too.
   window.clearTimeout(poll_start);
   window.clearInterval(polling);
}

function connect_controller(i) {
   stop_polling();
   let con = window.navigator.getGamepads()[i];
   if (con == null) {
      bridgeCommand(
         "contanki::message::Could not find controller. Please reconnect your controller and try again."
      );
      return;
   }
   bridgeCommand(`contanki::on_connect::${con.buttons.length}::${con.axes.length}::${con.id}`);
   connected_index = i;
   last_report = null;
   poll_start = setTimeout(() => (polling = setInterval(poll, POLL_MS)), 500);
}

function on_controller_disconnect(event) {
   stop_polling();
   if (connected_index != null) {
      bridgeCommand("contanki::on_disconnect::controller lost");
   }
   connected_index = null;
   let controllers = window.navigator.getGamepads();
   for (let i = 0; i < controllers.length; i++) {
      if (controllers[i] != null) {
         on_controller_connect(event);
         return;
      }
   }
}

function poll() {
   if (connected_index == null) {
      on_controller_disconnect("connected_index == null");
      return;
   }

   let con = window.navigator.getGamepads()[connected_index];

   try {
      if (!con.connected) {
         on_controller_disconnect("!con.connected");
         return;
      }
   } catch (err) {
      on_controller_disconnect(err);
      return;
   }

   let buttons = con.buttons.map((button) => button.pressed);
   let axes = Array.from(con.axes);
   let now = performance.now();
   if (now - last_report_at < HEARTBEAT_MS && !changed_since_report(buttons, axes)) {
      return;
   }
   last_report = { buttons, axes };
   last_report_at = now;
   bridgeCommand(`contanki::poll::${buttons}::${axes}`);
}

function changed_since_report(buttons, axes) {
   if (last_report == null || last_report.buttons.length != buttons.length) {
      return true;
   }
   for (let i = 0; i < buttons.length; i++) {
      if (buttons[i] != last_report.buttons[i]) return true;
   }
   for (let i = 0; i < axes.length; i++) {
      if (Math.abs(axes[i] - last_report.axes[i]) > AXIS_CHANGE) return true;
   }
   return false;
}

function mock_controller() {
   if (connected_index != null) {
      return;
   }
   bridgeCommand(`contanki::on_connect::10::4::mock_controller`);
   mock_index = 0;
   polling = setInterval(mock_poll, 50);
}

function mock_poll() {
   if (mock_index > 80) {
      mock_index = 0;
   }
   if (mock_index < 20) {
      bridgeCommand(
         `contanki::poll::true,false,false,false,false,false,false,false,false,false::0.5,0,0,0`
      );
   } else {
      bridgeCommand(
         `contanki::poll::false,false,false,false,false,false,false,false,false,false::0,0,0,0`
      );
   }
   mock_index += 1;
}

function get_controller_info() {
   let controllers = window.navigator.getGamepads();
   let ids = "";
   for (let i = 0; i < controllers.length; i++) {
      let con = controllers[i];
      if (con != null && con.connected) {
         ids += `%%%${con.id}%${con.buttons.length}%${con.axes.length}`;
      }
   }
   return ids;
}
