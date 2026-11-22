/**
 * The KTX2 transcoder worker.
 *
 * `createTranscoderWorker` replaces three's own creator so the worker can be
 * loaded from a URL instead of a blob -- a blob worker inherits the document's
 * CSP, and `script-src 'self' 'wasm-unsafe-eval'` refuses the Basis glue's
 * `eval`, which is the whole of the Safari failure.
 *
 * Replacing a creator means inheriting everything it did. The first version
 * did not, and dropped the `init` message: `KTX2Loader.BasisWorker` assigns
 * `transcoderPending` only in its `init` handler and its `transcode` handler
 * opens with `transcoderPending.then(...)`, so every KTX2 mesh failed in every
 * browser. These tests exist because that shipped.
 */

import { describe, expect, it, vi } from "vitest";

import { createTranscoderWorker } from "./ktx2Worker";

function fakeKtx2() {
  return {
    workerSourceURL: "blob:https://example.org/abc-123",
    workerConfig: { astcSupported: true, etc2Supported: false },
    transcoderBinary: new Uint8Array([1, 2, 3, 4]).buffer,
  };
}

function FakeWorker(url) {
  this.url = url;
  this.messages = [];
  this.transfers = [];
  this.listeners = {};
  this.postMessage = (msg, transfer) => {
    this.messages.push(msg);
    this.transfers.push(transfer);
  };
  this.addEventListener = (type, fn) => {
    (this.listeners[type] ||= []).push(fn);
  };
}

describe("createTranscoderWorker", () => {
  it("loads the worker from a same-origin URL, not the blob", () => {
    const ktx2 = fakeKtx2();
    const worker = createTranscoderWorker(
      ktx2,
      () => new FakeWorker("fallback"),
      undefined,
      FakeWorker,
      "/project/tirtha/",
    );
    expect(worker.url).toContain("/project/tirtha/basis/ktx2-bootstrap.js");
    // The blob three built is passed as a parameter for `importScripts`.
    expect(worker.url).toContain(encodeURIComponent(ktx2.workerSourceURL));
  });

  it("posts the init message three’s own creator would have posted", () => {
    const ktx2 = fakeKtx2();
    const worker = createTranscoderWorker(
      ktx2,
      () => new FakeWorker("fallback"),
      undefined,
      FakeWorker,
      "/project/tirtha/",
    );
    expect(worker.messages).toHaveLength(1);
    const [msg] = worker.messages;
    expect(msg.type).toBe("init");
    expect(msg.config).toBe(ktx2.workerConfig);
    expect(msg.transcoderBinary.byteLength).toBe(4);
  });

  it("transfers the binary rather than copying it", () => {
    const worker = createTranscoderWorker(
      fakeKtx2(),
      () => new FakeWorker("fallback"),
      undefined,
      FakeWorker,
      "/project/tirtha/",
    );
    const [transfer] = worker.transfers;
    expect(transfer).toEqual([worker.messages[0].transcoderBinary]);
  });

  it("sends a copy, so the pool can build a second worker", () => {
    const ktx2 = fakeKtx2();
    createTranscoderWorker(
      ktx2,
      () => new FakeWorker("f"),
      undefined,
      FakeWorker,
      "/",
    );
    // `.slice(0)` means the loader's own buffer is untouched by the transfer.
    expect(ktx2.transcoderBinary.byteLength).toBe(4);
    const second = createTranscoderWorker(
      ktx2,
      () => new FakeWorker("f"),
      undefined,
      FakeWorker,
      "/",
    );
    expect(second.messages[0].transcoderBinary.byteLength).toBe(4);
  });

  it("falls back to three’s creator when the URL worker cannot be built", () => {
    const fallback = new FakeWorker("fallback");
    const Boom = function () {
      throw new Error("blocked");
    };
    const worker = createTranscoderWorker(
      fakeKtx2(),
      () => fallback,
      undefined,
      Boom,
      "/",
    );
    expect(worker).toBe(fallback);
  });

  it("reports a worker that dies instead of leaving the load to hang", () => {
    const onError = vi.fn();
    const worker = createTranscoderWorker(
      fakeKtx2(),
      () => new FakeWorker("f"),
      onError,
      FakeWorker,
      "/",
    );
    expect(worker.listeners.error).toHaveLength(1);
    worker.listeners.error[0]({ message: "EvalError: refused" });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0].message).toContain("EvalError: refused");
  });
});
