import { execSync } from "child_process";
import os from "os";

interface PlatformInfo {
  platform: "mac" | "pc" | "linux";
  totalMemoryGb: number;
  embeddingProviders: string[];
  ollamaKeepAlive: string;
}

function detectPlatform(): PlatformInfo {
  const platform = os.platform();

  if (platform === "darwin") {
    const memBytes = getAppleSiliconMemory();
    const memGb = memBytes / (1024 ** 3);
    return {
      platform: "mac",
      totalMemoryGb: memGb,
      embeddingProviders: ["CoreMLExecutionProvider", "CPUExecutionProvider"],
      ollamaKeepAlive: "10m",
    };
  }

  const vramGb = getNvidiaVramGb();
  return {
    platform: "pc",
    totalMemoryGb: os.totalmem() / (1024 ** 3),
    embeddingProviders: ["CUDAExecutionProvider"],
    ollamaKeepAlive: "-1",
  };
}

function getAppleSiliconMemory(): number {
  try {
    const raw = execSync("sysctl hw.memsize").toString();
    return parseInt(raw.split(":")[1].trim(), 10);
  } catch {
    return os.totalmem();
  }
}

function getNvidiaVramGb(): number {
  try {
    const raw = execSync("nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits").toString();
    return parseInt(raw.trim(), 10) / 1024;
  } catch {
    return 0;
  }
}

export { detectPlatform, PlatformInfo };
