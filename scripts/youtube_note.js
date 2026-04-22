const { execFile } = require("child_process");
const path = require("path");

function execFileAsync(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    execFile(
      command,
      args,
      { maxBuffer: 20 * 1024 * 1024, ...options },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error(stderr || error.message));
          return;
        }
        resolve(stdout);
      }
    );
  });
}

module.exports = async function (tp, options = {}) {
  const vaultPath = tp.app.vault.adapter.getBasePath();
  const scriptPath = path.join(vaultPath, "scripts", "youtube_helper.py");

  const pythonCandidates = [
    options.pythonBin,
    "python3",
    "python",
    "py",
  ].filter(Boolean);

  let stdout = null;
  let lastError = null;

  for (const pythonBin of pythonCandidates) {
    try {
      stdout = await execFileAsync(
        pythonBin,
        [
          scriptPath,
          "--url", options.url || "",
          "--asset-folder", options.assetFolder || "Assets/YouTube",
          "--topics", options.topicsRaw || "",
          "--tags", options.tagsRaw || "",
        ],
        { cwd: vaultPath }
      );
      break;
    } catch (err) {
      lastError = err;
    }
  }

  if (!stdout) {
    throw new Error(
      `Could not run the Python helper.\n${lastError?.message || ""}`
    );
  }

  return JSON.parse(stdout);
};
