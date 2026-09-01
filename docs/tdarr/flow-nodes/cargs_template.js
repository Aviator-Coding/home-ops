// Node: "AV1 tuning (encoder-aware) - __LABEL__"
// Replaces ffmpegCommandCustomArguments, which appended QSV-only options
// unconditionally. On a CPU job the encoder is libsvtav1, which rejects
// "-preset medium" and has no -global_quality / -look_ahead, so ffmpeg died at
// encoder init and every CPU transcode failed (88/88 since 2026-08-29).
//
// Emits tuning matched to the encoder the preceding SetVideoEncoder node
// actually chose. Fails CLOSED: an unrecognised encoder dead-ends (output 2,
// which has no edge) rather than shipping arguments it may reject.
//
// It is also the 4K CPU GUARD. Measured 2026-08-31 on this node, a 3840x2160
// `libsvtav1 -preset 8` encode peaks at ~7,100 MiB RSS against the container's
// 4Gi limit and OOM-kills the whole tdarr-tdarr-node container (exit 137),
// taking whatever the GPU worker was doing down with it. The same file on
// av1_qsv peaks at ~1,225 MiB and is faster (54 vs 43 fps). The B70 is mounted
// into this container unconditionally (devic.es/b70-vaapi), so when a CPU
// worker is about to run libsvtav1 on a frame too large to hold, this rewrites
// the already-chosen encoder to av1_qsv. Files it does not touch keep the CPU
// fallback PR #1443 added.
//
// The guard lives HERE, not in an earlier node, for two measured reasons:
//   * `args.workerType` mutated in an earlier customFunction does NOT reach
//     ffmpegCommandSetVideoEncoder - each node is handed its own worker
//     context, so flipping it upstream is silently ignored (observed
//     2026-08-31: guard logged the flip, ffmpeg still ran `-c:0 libsvtav1`).
//   * The community `tagsRequeue` node cannot be used to hand the file to a
//     GPU worker instead. It does set the staged status to
//     `queued:requireGPU` (observed), but when the flow then ends the job is
//     finalised "Not required", the staged row is deleted and the file leaves
//     the queue permanently. Measured three times, once in isolation with
//     health checks suppressed. The require* routing in getStagedFiles is not
//     Pro-gated (unlike nodeTags) - it is the flow-end finalisation that
//     discards it.
// It decides on the video's own PIXEL COUNT converted to a measured memory
// need, never on filename, library or path, so 4K arriving by ANY route is
// covered.

module.exports = async (args) => {
  const QUALITY = __QUALITY__;   // av1_qsv -global_quality, libsvtav1 -crf
  const SVT_PRESET = '8';        // libsvtav1 -preset, 0-13, lower = slower/better.
                                 // 8, not 6: this is the FALLBACK worker, so keep
                                 // throughput up during a GPU outage. Tunable.
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

  // --- 4K CPU guard: keep libsvtav1 away from frames it cannot hold ---
  // Calibration measured 2026-08-31 on tdarr-tdarr-node (talos-3):
  //   3840x2160 = 8.2944 Mpx -> ~7100 MiB RSS for libsvtav1 -preset 8
  const MIB_PER_MEGAPIXEL = 856;      // 7100 / 8.2944
  const CPU_FFMPEG_BUDGET_MIB = 2800; // 4096 MiB limit, minus node + healthcheck
  if (encoder === 'libsvtav1') {
    // Dimensions come from the streams this command will actually encode, the
    // same list the encoder rewrite below walks - not from inputFileObj, so the
    // guard and the rewrite can never disagree about which stream is the video.
    let pixels = 0;
    for (let i = 0; i < streams.length; i += 1) {
      const s = streams[i] || {};
      if (String(s.codec_type) !== 'video') continue;
      if (String(s.codec_name).toLowerCase() === 'mjpeg') continue; // cover art
      if (s.removed) continue;
      const area = Number(s.width || 0) * Number(s.height || 0);
      if (area > pixels) pixels = area;
    }
    const estimateMiB = Math.round((pixels / 1000000) * MIB_PER_MEGAPIXEL);
    // Unknown dimensions are treated as too big: failing towards the GPU costs
    // throughput, failing towards libsvtav1 costs the whole node.
    if (pixels === 0 || estimateMiB > CPU_FFMPEG_BUDGET_MIB) {
      for (let i = 0; i < streams.length; i += 1) {
        const st = streams[i];
        if (st.codec_type !== 'video' || st.codec_name === 'mjpeg') continue;
        const oa = st.outputArgs || [];
        for (let j = 0; j < oa.length - 1; j += 1) {
          if (String(oa[j]).indexOf('-c:') === 0 && String(oa[j + 1]) === 'libsvtav1') {
            oa[j + 1] = 'av1_qsv';
          }
        }
      }
      args.jobLog('AV1 tuning: 4K CPU guard - pixels=' + pixels +
                  ' estCpuRss=' + (pixels === 0 ? 'unknown' : estimateMiB + 'MiB') +
                  ' budget=' + CPU_FFMPEG_BUDGET_MIB +
                  'MiB -> libsvtav1 would OOM this container, encoding av1_qsv instead');
      encoder = 'av1_qsv';
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
