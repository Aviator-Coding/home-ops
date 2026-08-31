// Node: "AV1 tuning (encoder-aware) - __LABEL__"
// Replaces ffmpegCommandCustomArguments, which appended QSV-only options
// unconditionally. On a CPU job the encoder is libsvtav1, which rejects
// "-preset medium" and has no -global_quality / -look_ahead, so ffmpeg died at
// encoder init and every CPU transcode failed (88/88 since 2026-08-29).
//
// Emits tuning matched to the encoder the preceding SetVideoEncoder node
// actually chose. Fails CLOSED: an unrecognised encoder dead-ends (output 2,
// which has no edge) rather than shipping arguments it may reject.

module.exports = async (args) => {
  const QUALITY = __QUALITY__;   // av1_qsv -global_quality, libsvtav1 -crf
  const SVT_PRESET = '6';        // libsvtav1 -preset, 0-13, lower = slower/better
  const HDR = __HDR__;           // emit HDR10 signalling tags

  const cmd = args.variables && args.variables.ffmpegCommand;
  if (!cmd || !Array.isArray(cmd.overallOuputArguments)) {
    args.jobLog('AV1 tuning: ffmpegCommand not initialised - FAIL (will not encode)');
    return { outputFileObj: args.inputFileObj, outputNumber: 2, variables: args.variables };
  }

  // Read back the encoder chosen upstream: SetVideoEncoder pushes
  // ['-c:{outputIndex}', <encoder>] onto the video stream's outputArgs.
  let encoder = '';
  const streams = cmd.streams || [];
  for (let i = 0; i < streams.length && !encoder; i += 1) {
    const s = streams[i];
    if (s.codec_type !== 'video' || s.codec_name === 'mjpeg') continue;
    const oa = s.outputArgs || [];
    for (let j = 0; j < oa.length - 1; j += 1) {
      if (String(oa[j]).indexOf('-c:') === 0) { encoder = String(oa[j + 1]); break; }
    }
  }

  const out = [];
  if (HDR) {
    out.push('-color_primaries', 'bt2020', '-color_trc', 'smpte2084',
             '-colorspace', 'bt2020nc', '-color_range', 'tv');
  }

  if (encoder === 'av1_qsv') {
    out.push('-preset', 'medium', '-global_quality', String(QUALITY), '-look_ahead', '1');
  } else if (encoder === 'libsvtav1') {
    out.push('-preset', SVT_PRESET, '-crf', String(QUALITY));
  } else {
    args.jobLog('AV1 tuning: unrecognised encoder "' + encoder + '" - FAIL (will not encode)');
    return { outputFileObj: args.inputFileObj, outputNumber: 2, variables: args.variables };
  }

  cmd.overallOuputArguments.push.apply(cmd.overallOuputArguments, out);
  args.jobLog('AV1 tuning: encoder="' + encoder + '" quality=' + QUALITY +
              ' hdr=' + HDR + ' args=[' + out.join(' ') + ']');

  return { outputFileObj: args.inputFileObj, outputNumber: 1, variables: args.variables };
};
