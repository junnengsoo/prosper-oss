import type { NormalizedMessage } from "./normalize.js";

export type BurstWaitState = {
  waitMs: number;
  burstMode: string;
};

type PendingBuffer<TimerHandle> = {
  messages: NormalizedMessage[];
  timer: TimerHandle | null;
  waitState: BurstWaitState | null;
};

type ForwardContext =
  | { kind: "batch"; chatJid: string; messages: NormalizedMessage[] }
  | { kind: "immediate"; message: NormalizedMessage };

export type InboundBurstBufferDeps<TimerHandle> = {
  fetchBurstState: (chatJid: string) => Promise<BurstWaitState>;
  postInboundBatch: (messages: NormalizedMessage[]) => Promise<void>;
  setTimer?: (callback: () => void, ms: number) => TimerHandle;
  clearTimer?: (timer: TimerHandle) => void;
  onBatchForwarded?: (chatJid: string, messages: NormalizedMessage[]) => void;
  onImmediateForwarded?: (message: NormalizedMessage) => void;
  onForwardError?: (error: unknown, context: ForwardContext) => void;
};

export class InboundBurstBuffers<TimerHandle = ReturnType<typeof setTimeout>> {
  private readonly buffers = new Map<string, PendingBuffer<TimerHandle>>();
  private readonly fetchBurstState: (chatJid: string) => Promise<BurstWaitState>;
  private readonly postInboundBatch: (messages: NormalizedMessage[]) => Promise<void>;
  private readonly setTimer: (callback: () => void, ms: number) => TimerHandle;
  private readonly clearTimer: (timer: TimerHandle) => void;
  private readonly onBatchForwarded?: (chatJid: string, messages: NormalizedMessage[]) => void;
  private readonly onImmediateForwarded?: (message: NormalizedMessage) => void;
  private readonly onForwardError?: (error: unknown, context: ForwardContext) => void;

  constructor(deps: InboundBurstBufferDeps<TimerHandle>) {
    this.fetchBurstState = deps.fetchBurstState;
    this.postInboundBatch = deps.postInboundBatch;
    this.setTimer = deps.setTimer ?? ((callback, ms) => setTimeout(callback, ms) as TimerHandle);
    this.clearTimer = deps.clearTimer ?? ((timer) => clearTimeout(timer as ReturnType<typeof setTimeout>));
    this.onBatchForwarded = deps.onBatchForwarded;
    this.onImmediateForwarded = deps.onImmediateForwarded;
    this.onForwardError = deps.onForwardError;
  }

  chatCount(): number {
    return this.buffers.size;
  }

  messageCount(): number {
    return Array.from(this.buffers.values()).reduce((total, buffer) => total + buffer.messages.length, 0);
  }

  async receive(message: NormalizedMessage): Promise<void> {
    if (message.fromMe) {
      await this.flush(message.chatJid);
      await this.forwardImmediate(message);
      return;
    }

    const existing = this.buffers.get(message.chatJid);
    if (existing) {
      existing.messages.push(message);
      if (existing.waitState && existing.timer) {
        this.clearTimer(existing.timer);
        this.scheduleFlush(message.chatJid, existing, existing.waitState);
      }
      return;
    }

    const buffer: PendingBuffer<TimerHandle> = {
      messages: [message],
      timer: null,
      waitState: null,
    };
    this.buffers.set(message.chatJid, buffer);

    const waitState = await this.fetchBurstState(message.chatJid);
    if (this.buffers.get(message.chatJid) !== buffer) return;
    await this.scheduleOrFlush(message.chatJid, buffer, waitState);
  }

  async flush(chatJid: string): Promise<void> {
    const buffer = this.buffers.get(chatJid);
    if (!buffer) return;
    this.buffers.delete(chatJid);
    if (buffer.timer) {
      this.clearTimer(buffer.timer);
      buffer.timer = null;
    }

    const messages = [...buffer.messages].sort((a, b) => a.timestampMs - b.timestampMs);
    if (messages.length === 0) return;
    try {
      await this.postInboundBatch(messages);
      this.onBatchForwarded?.(chatJid, messages);
    } catch (error) {
      this.onForwardError?.(error, { kind: "batch", chatJid, messages });
    }
  }

  async flushAll(): Promise<void> {
    for (const chatJid of Array.from(this.buffers.keys())) {
      await this.flush(chatJid);
    }
  }

  private async scheduleOrFlush(
    chatJid: string,
    buffer: PendingBuffer<TimerHandle>,
    waitState: BurstWaitState,
  ): Promise<void> {
    buffer.waitState = waitState;
    if (waitState.waitMs <= 0) {
      await this.flush(chatJid);
      return;
    }
    this.scheduleFlush(chatJid, buffer, waitState);
  }

  private scheduleFlush(chatJid: string, buffer: PendingBuffer<TimerHandle>, waitState: BurstWaitState): void {
    buffer.waitState = waitState;
    buffer.timer = this.setTimer(() => {
      void this.flush(chatJid);
    }, Math.max(0, waitState.waitMs));
  }

  private async forwardImmediate(message: NormalizedMessage): Promise<void> {
    try {
      await this.postInboundBatch([message]);
      this.onImmediateForwarded?.(message);
    } catch (error) {
      this.onForwardError?.(error, { kind: "immediate", message });
    }
  }
}
