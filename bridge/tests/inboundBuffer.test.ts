import assert from "node:assert/strict";

import { InboundBurstBuffers, type BurstWaitState } from "../src/inboundBuffer.js";
import type { NormalizedMessage } from "../src/normalize.js";

type ScheduledTask = {
  id: number;
  ms: number;
  callback: () => void;
  active: boolean;
};

function message(overrides: Partial<NormalizedMessage> = {}): NormalizedMessage {
  return {
    chatJid: "6599999999@s.whatsapp.net",
    senderJid: "6599999999@s.whatsapp.net",
    messageId: "message-1",
    timestampMs: 1_700_000_000_000,
    fromMe: false,
    isGroup: false,
    text: "Hi is this available?",
    rawType: "conversation",
    ...overrides,
  };
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((innerResolve) => {
    resolve = innerResolve;
  });
  return { promise, resolve };
}

async function tick(): Promise<void> {
  await Promise.resolve();
}

class ManualScheduler {
  private nextId = 1;
  readonly tasks: ScheduledTask[] = [];

  setTimeout(callback: () => void, ms: number): ScheduledTask {
    const task = { id: this.nextId, ms, callback, active: true };
    this.nextId += 1;
    this.tasks.push(task);
    return task;
  }

  clearTimeout(task: ScheduledTask): void {
    task.active = false;
  }

  runNext(): void {
    const task = this.tasks.find((candidate) => candidate.active);
    assert.ok(task, "expected an active scheduled task");
    task.active = false;
    task.callback();
  }
}

function createHarness(states: Map<string, ReturnType<typeof deferred<BurstWaitState>>>) {
  const scheduler = new ManualScheduler();
  const batches: NormalizedMessage[][] = [];
  const buffers = new InboundBurstBuffers<ScheduledTask>({
    fetchBurstState(chatJid) {
      const state = states.get(chatJid);
      assert.ok(state, `missing deferred state for ${chatJid}`);
      return state.promise;
    },
    postInboundBatch(messages) {
      batches.push(messages);
      return Promise.resolve();
    },
    setTimer: (callback, ms) => scheduler.setTimeout(callback, ms),
    clearTimer: (task) => scheduler.clearTimeout(task),
  });
  return { batches, buffers, scheduler };
}

{
  const state = deferred<BurstWaitState>();
  const { batches, buffers, scheduler } = createHarness(new Map([["6599999999@s.whatsapp.net", state]]));

  const first = buffers.receive(message({ messageId: "later", timestampMs: 1_700_000_001_000, text: "Later" }));
  await tick();
  await buffers.receive(message({ messageId: "earlier", timestampMs: 1_700_000_000_000, text: "Earlier" }));
  state.resolve({ waitMs: 50, burstMode: "triage" });
  await first;
  assert.equal(batches.length, 0);

  scheduler.runNext();
  await tick();

  assert.equal(batches.length, 1);
  assert.deepEqual(
    batches[0].map((item) => item.messageId),
    ["earlier", "later"],
  );
}

{
  const one = deferred<BurstWaitState>();
  const two = deferred<BurstWaitState>();
  const { batches, buffers, scheduler } = createHarness(
    new Map([
      ["one@s.whatsapp.net", one],
      ["two@s.whatsapp.net", two],
    ]),
  );

  const first = buffers.receive(message({ chatJid: "one@s.whatsapp.net", senderJid: "one@s.whatsapp.net", messageId: "one-1" }));
  const second = buffers.receive(message({ chatJid: "two@s.whatsapp.net", senderJid: "two@s.whatsapp.net", messageId: "two-1" }));
  await tick();

  two.resolve({ waitMs: 25, burstMode: "triage" });
  await second;
  scheduler.runNext();
  await tick();

  assert.deepEqual(batches.map((batch) => batch.map((item) => item.messageId)), [["two-1"]]);

  one.resolve({ waitMs: 25, burstMode: "triage" });
  await first;
  scheduler.runNext();
  await tick();

  assert.deepEqual(batches.map((batch) => batch.map((item) => item.messageId)), [["two-1"], ["one-1"]]);
}

{
  const state = deferred<BurstWaitState>();
  const { batches, buffers } = createHarness(new Map([["6599999999@s.whatsapp.net", state]]));

  const first = buffers.receive(message({ messageId: "zero-1" }));
  await tick();
  await buffers.receive(message({ messageId: "zero-2", timestampMs: 1_700_000_001_000 }));
  state.resolve({ waitMs: 0, burstMode: "triage" });
  await first;
  await tick();

  assert.deepEqual(batches.map((batch) => batch.map((item) => item.messageId)), [["zero-1", "zero-2"]]);
}

{
  const state = deferred<BurstWaitState>();
  const { batches, buffers } = createHarness(new Map([["6599999999@s.whatsapp.net", state]]));

  const first = buffers.receive(message({ messageId: "tenant-1" }));
  await tick();
  await buffers.receive(message({ fromMe: true, senderJid: "me@s.whatsapp.net", messageId: "manual-1", text: "I will reply manually." }));
  state.resolve({ waitMs: 50, burstMode: "triage" });
  await first;
  await tick();

  assert.deepEqual(batches.map((batch) => batch.map((item) => item.messageId)), [["tenant-1"], ["manual-1"]]);
}

{
  const state = deferred<BurstWaitState>();
  const { batches, buffers, scheduler } = createHarness(new Map([["6599999999@s.whatsapp.net", state]]));

  const first = buffers.receive(message({ messageId: "tenant-1" }));
  await tick();
  await buffers.flush("6599999999@s.whatsapp.net");
  state.resolve({ waitMs: 50, burstMode: "triage" });
  await first;
  await tick();

  assert.deepEqual(batches.map((batch) => batch.map((item) => item.messageId)), [["tenant-1"]]);
  assert.equal(scheduler.tasks.filter((task) => task.active).length, 0);
}

console.log("bridge inbound buffer tests passed");
