// Unit-prove the conform node's decisions against the seven masters' REAL
// ffProbeData, read-only. Simulates ffmpegCommandStart's stream construction.
const fs = require('fs');
const path = '/tmp/subconform_mod.js';
fs.writeFileSync(path, fs.readFileSync('subconform.js', 'utf8'));
const node = require(path);
const masters = JSON.parse(fs.readFileSync('masters_probe.json', 'utf8'));

(async () => {
  for (const [name, m] of Object.entries(masters)) {
    // Same normalisation ffmpegCommandStart performs.
    const streams = m.streams.map((s, i) => {
      const n = Object.assign({}, s);
      if (Number((s.disposition || {}).attached_pic) === 1) n.codec_type = 'attachment';
      return Object.assign(n, { index: i, removed: false, mapArgs: ['-map', `0:${i}`], inputArgs: [], outputArgs: [] });
    });
    const logs = [];
    const args = {
      inputFileObj: { _id: m.id },
      variables: { ffmpegCommand: { streams } },
      jobLog: (s) => logs.push(s),
    };
    const before = streams.length;
    const r = await node(args);
    const kept = streams.filter((s) => !s.removed);
    const conv = streams.filter((s) => s.outputArgs.join(' ').includes('srt'));
    const cnt = (arr, t) => arr.filter((s) => s.codec_type === t).length;
    console.log(`\n### ${name}`);
    console.log(`    outputNumber=${r.outputNumber}  ${logs.join(' | ')}`);
    console.log(`    streams       ${before} -> ${kept.length}`);
    console.log(`    subtitles     ${cnt(streams,'subtitle')} -> ${cnt(kept,'subtitle')}   (mov_text converted to srt: ${conv.length})`);
    console.log(`    audio         ${cnt(streams,'audio')} -> ${cnt(kept,'audio')}`);
    console.log(`    video         ${cnt(streams,'video')} -> ${cnt(kept,'video')}`);
    const dropped = streams.filter((s) => s.removed)
      .map((s) => `${s.codec_type}/${s.codec_name}${s.codec_type==='video'?`(${s.width}x${s.height})`:''}`);
    console.log(`    dropped       ${dropped.length ? dropped.join(', ') : '(none)'}`);
  }
})();
