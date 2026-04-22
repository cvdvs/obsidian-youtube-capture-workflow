<%*
const ASSET_FOLDER = "Assets/YouTube";

const url = await tp.system.prompt("Paste the YouTube URL");
if (!url) throw new Error("No YouTube URL provided.");

const topicsRaw = await tp.system.prompt("Topics (comma-separated)", "");
const tagsRaw = await tp.system.prompt("Tags (comma-separated)", "youtube");

const data = await tp.user.youtube_note(tp, {
  url,
  topicsRaw,
  tagsRaw,
  assetFolder: ASSET_FOLDER,
  pythonBin: "python3",
});

const quote = (value) => JSON.stringify(value ?? "");
const yamlList = (arr) =>
  Array.isArray(arr) && arr.length
    ? `[${arr.map((v) => JSON.stringify(v)).join(", ")}]`
    : "[]";

const imageBlock = data.thumbnail_relative
  ? `![[${data.thumbnail_relative}]]\n\n`
  : "";

const summaryModelLine = data.summary_model
  ? `summary_model: ${quote(data.summary_model)}\n`
  : "";

await tp.file.rename(data.note_filename);

tR += `---
type: youtube
title: ${quote(data.title)}
url: ${quote(data.url)}
video_id: ${quote(data.video_id)}
creator: ${quote(data.creator)}
topics: ${yamlList(data.topics)}
tags: ${yamlList(data.tags)}
thumbnail: ${quote(data.thumbnail_relative)}
${summaryModelLine}transcript_language: ${quote(data.transcript_language)}
transcript_source: ${quote(data.transcript_source)}
created: ${quote(tp.date.now("YYYY-MM-DD"))}
---

${imageBlock}> [!abstract] Summary
${data.summary_blockquote || "> TODO"}

> [!info]- Transcript
${data.transcript_blockquote}

---

## Notes

`;
%>
