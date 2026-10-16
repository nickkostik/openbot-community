import { Worker } from "node:worker_threads";
import { fileURLToPath } from "node:url";

export type LogReadMethod = "listRequests" | "getRequest" | "statsNow" | "facetsNow" | "usageNow" | "queryEvents";
const METHODS = new Set<LogReadMethod>(["listRequests", "getRequest", "statsNow", "facetsNow", "usageNow", "queryEvents"]);

// JSON parsing, sorting and aggregation of a large log must not run on the
// unified UI/hop event loop. One reusable worker bounds CPU and memory; identical
// simultaneous polls share a job. Writes and maintenance stay with their owner.
const PROGRAM = `
const {parentPort,workerData}=require('node:worker_threads');
const fs=require('node:fs'),path=require('node:path');
const reader=require(workerData.payload);
let revision='';
parentPort.on('message',({id,method,arg})=>{
  try {
    const next=['openbot-requests.jsonl','openbot-events.jsonl','openbot-logs.json'].map(name=>{
      try {const s=fs.statSync(path.join(workerData.sandData,name));return [s.ino,s.size,s.mtimeMs].join(':');}
      catch {return 'missing';}
    }).join('|');
    if(next!==revision){reader.invalidateReadAggregates();revision=next;}
    parentPort.postMessage({id,value:reader[method](arg)});
  } catch {parentPort.postMessage({id,error:'Log reader failed'});}
});
`;

export function createLogReader(options: {
  sandData: string;
  payloadPath?: string;
  maxPending?: number;
  timeoutMs?: number;
}) {
  let worker: Worker | undefined;
  let sequence = 0;
  let closed = false;
  const jobs = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void; timer: ReturnType<typeof setTimeout> }>();
  const shared = new Map<string, Promise<unknown>>();

  function fail(current: Worker, error: Error) {
    if (worker !== current) return;
    worker = undefined;
    for (const job of jobs.values()) { clearTimeout(job.timer); job.reject(error); }
    jobs.clear();
    void current.terminate();
  }

  function instance(): Worker {
    if (worker) return worker;
    const current = new Worker(PROGRAM, {
      eval: true,
      execArgv: [],
      workerData: { sandData: options.sandData, payload: options.payloadPath ?? fileURLToPath(new URL("../../payload/request-log.cjs", import.meta.url)) },
      env: { ...process.env, OPENBOT_SAND_DATA: options.sandData, OPENBOT_LOG_READER_CACHE: "1" },
    });
    worker = current;
    current.on("message", (reply: { id: number; value?: unknown; error?: string }) => {
      const job = jobs.get(reply.id);
      if (!job) return;
      jobs.delete(reply.id);
      clearTimeout(job.timer);
      if (reply.error) job.reject(new Error(reply.error)); else job.resolve(reply.value);
      if (!jobs.size) current.unref();
    });
    current.once("error", () => fail(current, new Error("Log worker stopped")));
    current.once("exit", () => fail(current, new Error("Log worker exited")));
    current.unref();
    return current;
  }

  function read<T = unknown>(method: LogReadMethod, arg?: unknown): Promise<T> {
    if (closed || !METHODS.has(method)) return Promise.reject(new Error("Log reader is unavailable"));
    const key = JSON.stringify([method, arg]);
    const duplicate = shared.get(key);
    if (duplicate) return duplicate as Promise<T>;
    if (jobs.size >= (options.maxPending ?? 32)) return Promise.reject(new Error("Log reader is busy; retry shortly"));
    const current = instance();
    const id = ++sequence;
    const job = new Promise<unknown>((resolve, reject) => {
      const timer = setTimeout(() => fail(current, new Error("Log reader timed out")), options.timeoutMs ?? 30_000);
      jobs.set(id, { resolve, reject, timer });
      current.ref();
      current.postMessage({ id, method, arg });
    }).finally(() => shared.delete(key));
    shared.set(key, job);
    return job as Promise<T>;
  }

  return {
    read,
    async close() {
      closed = true;
      if (worker) fail(worker, new Error("Log reader closed"));
    },
  };
}
