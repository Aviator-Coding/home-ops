// Node: "Conform streams for matroska (convert, do not drop)"
// Place: AFTER Set Container mkv, BEFORE Set Video Encoder.
// Output 1 -> continue
// Output 2 -> FAIL (dead end, nothing encoded)
//
// Why this exists: matroska cannot mux mov_text, and Tdarr's own
// `forceConform` option DELETES those streams (1-46 subtitle tracks on the
// parked 4K masters). This converts them to subrip instead, so every track
// survives, and drops only the streams matroska genuinely cannot carry and
// which have no text equivalent.

module.exports = async (args) => {
  const cmd = args.variables && args.variables.ffmpegCommand;
  if (!cmd || !Array.isArray(cmd.streams)) {
    args.jobLog('Stream conform: ffmpegCommand not initialised - FAIL (will not encode)');
    return { outputFileObj: args.inputFileObj, outputNumber: 2, variables: args.variables };
  }

  const UNMUXABLE = ['bin_data', 'timed_id3', 'eia_608'];
  const before = { video: 0, audio: 0, subtitle: 0, other: 0 };
  let convertedSubs = 0;
  let droppedData = 0;
  let droppedArt = 0;

  for (let i = 0; i < cmd.streams.length; i += 1) {
    const s = cmd.streams[i];
    if (s.removed) continue;
    const type = String(s.codec_type || '');
    const name = String(s.codec_name || '').toLowerCase();
    if (before[type] === undefined) before.other += 1; else before[type] += 1;

    // matroska has no mov_text mapping. Convert to subrip, never delete.
    if (type === 'subtitle' && name === 'mov_text') {
      s.outputArgs.push('-c:{outputIndex}', 'srt');
      convertedSubs += 1;
      continue;
    }

    // No text equivalent and matroska cannot carry them.
    if (type === 'data' || UNMUXABLE.indexOf(name) !== -1) {
      s.removed = true;
      droppedData += 1;
      continue;
    }

    // Cover art with no dimensions -> matroska "dimensions not set".
    // Drop only the broken art stream; valid art (e.g. 600x900) is kept.
    if (type === 'video' && name === 'mjpeg') {
      const w = Number(s.width || 0);
      const h = Number(s.height || 0);
      if (!w || !h) {
        s.removed = true;
        droppedArt += 1;
        continue;
      }
    }
  }

  args.jobLog(
    'Stream conform: in v=' + before.video + ' a=' + before.audio +
      ' s=' + before.subtitle + ' other=' + before.other +
      ' | mov_text->srt=' + convertedSubs +
      ' droppedData=' + droppedData + ' dropped0x0Art=' + droppedArt,
  );

  return { outputFileObj: args.inputFileObj, outputNumber: 1, variables: args.variables };
};
