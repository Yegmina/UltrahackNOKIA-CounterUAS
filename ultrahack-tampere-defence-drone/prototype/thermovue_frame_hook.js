'use strict';

/*
 * Frida agent for ThermoVue raw sensor packets.
 *
 * Loaded by thermovue_frida_bridge.py. It hooks ThermoVue's Java IR frame
 * callback and sends selected 4,863,232-byte packets to the laptop over
 * Frida's USB transport. The Python runner then forwards those bytes to the
 * live viewer TCP socket.
 */

const bridgeConfig = globalThis.BRIDGE_CONFIG || {};
const EXPECTED_LENGTH = bridgeConfig.expectedLength || 4863232;
const EVERY_N_FRAMES = Math.max(1, bridgeConfig.every || 1);
const MAX_FRAMES = bridgeConfig.maxFrames || 0;

const CALLBACK_CLASSES = [
  'com.energy.dualmodule.sdk.uvc.UvcNativeCamDualFusionPreviewManager$3',
  'com.energy.tc2c.sop.camera.UvcNativeCamDualCalManager$mIIrFrameCallback$1',
];

let totalSeen = 0;
let totalForwarded = 0;
const hooked = {};

function postStatus(text, extra) {
  send(Object.assign({ type: 'status', text }, extra || {}));
}

function postError(text, extra) {
  send(Object.assign({ type: 'error', text }, extra || {}));
}

function copyJavaByteArray(byteArray, length) {
  const env = Java.vm.getEnv();
  const buffer = Memory.alloc(length);
  env.getByteArrayRegion(byteArray, 0, length, buffer);
  return buffer.readByteArray(length);
}

function maybeForwardFrame(className, frame, length) {
  totalSeen += 1;

  if (length !== EXPECTED_LENGTH) {
    if (totalSeen <= 10 || totalSeen % 100 === 0) {
      postStatus('Skipping unexpected frame length', {
        className,
        length,
        expectedLength: EXPECTED_LENGTH,
        seen: totalSeen,
      });
    }
    return;
  }

  if (totalSeen % EVERY_N_FRAMES !== 0) {
    return;
  }

  if (MAX_FRAMES > 0 && totalForwarded >= MAX_FRAMES) {
    return;
  }

  const payload = copyJavaByteArray(frame, length);
  totalForwarded += 1;
  send(
    {
      type: 'frame',
      className,
      length,
      seen: totalSeen,
      forwarded: totalForwarded,
      timestampMs: Date.now(),
    },
    payload
  );
}

function hookCallbackClass(className) {
  if (hooked[className]) {
    return true;
  }

  let klass;
  try {
    klass = Java.use(className);
  } catch (error) {
    return false;
  }

  let overload;
  try {
    overload = klass.onFrame.overload('[B', 'int');
  } catch (error) {
    postError('Class exists but onFrame(byte[], int) overload was not found', {
      className,
      error: String(error),
    });
    return false;
  }

  overload.implementation = function (frame, length) {
    const result = overload.call(this, frame, length);
    try {
      maybeForwardFrame(className, frame, length);
    } catch (error) {
      postError('Frame forwarding failed', {
        className,
        error: String(error),
      });
    }
    return result;
  };

  hooked[className] = true;
  postStatus('Hooked frame callback', { className });
  return true;
}

function tryHookAll() {
  let hookedAny = false;
  CALLBACK_CLASSES.forEach((className) => {
    hookedAny = hookCallbackClass(className) || hookedAny;
  });
  return hookedAny;
}

function main() {
  Java.perform(() => {
    postStatus('ThermoVue Frida bridge loaded', {
      expectedLength: EXPECTED_LENGTH,
      every: EVERY_N_FRAMES,
      maxFrames: MAX_FRAMES,
    });

    if (tryHookAll()) {
      return;
    }

    postStatus('Frame callback class not loaded yet; polling...');
    const timer = setInterval(() => {
      if (tryHookAll()) {
        clearInterval(timer);
      }
    }, 1000);
  });
}

setImmediate(main);
