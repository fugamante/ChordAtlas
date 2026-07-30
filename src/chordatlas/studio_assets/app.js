(() => {
  "use strict";

  const fileInput = document.querySelector("#audio-file");
  const authorization = document.querySelector("#authorization");
  const importButton = document.querySelector("#import");
  const status = document.querySelector("#status");
  const remoteName = document.querySelector("#remote-name");
  const remoteUrl = document.querySelector("#remote-url");
  const toggleRemoteUrl = document.querySelector("#toggle-remote-url");
  const remoteAuthorization = document.querySelector("#remote-authorization");
  const acquireButton = document.querySelector("#acquire");
  const acquisitionProgress = document.querySelector("#acquisition-progress");
  const acquisitionStatus = document.querySelector("#acquisition-status");
  const cancelAcquisitionButton = document.querySelector("#cancel-acquisition");
  const retryAcquisitionButton = document.querySelector("#retry-acquisition");
  const forgetAcquisitionButton = document.querySelector("#forget-acquisition");
  const acquisitionDetails = document.querySelector("#acquisition-details");
  const acquisitionMetadata = document.querySelector("#acquisition-metadata");
  const sourceSelect = document.querySelector("#source-select");
  const metadata = document.querySelector("#metadata");
  const canvas = document.querySelector("#waveform");
  const cursor = document.querySelector("#cursor");
  const audio = document.querySelector("#audio");
  const playButton = document.querySelector("#play");
  const pauseButton = document.querySelector("#pause");
  const seek = document.querySelector("#seek");
  const position = document.querySelector("#position");
  const loopFieldset = document.querySelector(".loop-controls");
  const loopStart = document.querySelector("#loop-start");
  const loopEnd = document.querySelector("#loop-end");
  const setLoopButton = document.querySelector("#set-loop");
  const clearLoopButton = document.querySelector("#clear-loop");
  const loopState = document.querySelector("#loop-state");
  const analysisScope = document.querySelector("#analysis-scope");
  const analyzeButton = document.querySelector("#analyze");
  const cancelAnalysisButton = document.querySelector("#cancel-analysis");
  const retryAnalysisButton = document.querySelector("#retry-analysis");
  const analysisRunSelect = document.querySelector("#analysis-run");
  const analysisProgress = document.querySelector("#analysis-progress");
  const analysisStatus = document.querySelector("#analysis-status");
  const analysisMetadata = document.querySelector("#analysis-metadata");
  const analysisSummary = document.querySelector("#analysis-summary");
  const candidateLane = document.querySelector("#candidate-lane");
  const startReviewButton = document.querySelector("#start-review");
  const reviewSessionSelect = document.querySelector("#review-session");
  const undoReviewButton = document.querySelector("#undo-review");
  const redoReviewButton = document.querySelector("#redo-review");
  const resetReviewButton = document.querySelector("#reset-review");
  const acceptBoundariesButton = document.querySelector("#accept-boundaries");
  const finishReviewButton = document.querySelector("#finish-review");
  const reviewStatus = document.querySelector("#review-status");
  const reviewConflictActions = document.querySelector("#review-conflict-actions");
  const retryReviewEditButton = document.querySelector("#retry-review-edit");
  const discardReviewEditButton = document.querySelector("#discard-review-edit");
  const finishReason = document.querySelector("#finish-reason");
  const reviewPhase = document.querySelector("#review-phase");
  const reviewMetrics = document.querySelector("#review-metrics");
  const reviewLane = document.querySelector("#review-lane");
  const noChordControls = document.querySelector("#no-chord-controls");
  const noChordStart = document.querySelector("#no-chord-start");
  const noChordEnd = document.querySelector("#no-chord-end");
  const insertNoChordButton = document.querySelector("#insert-no-chord");
  const sectionControls = document.querySelector("#section-controls");
  const sectionLabel = document.querySelector("#section-label");
  const sectionFrame = document.querySelector("#section-frame");
  const addSectionButton = document.querySelector("#add-section");
  const sectionList = document.querySelector("#section-list");
  const promotionPhase = document.querySelector("#promotion-phase");
  const promotionControls = document.querySelector("#promotion-controls");
  const promotionTitleInput = document.querySelector("#promotion-title-input");
  const promotionArtist = document.querySelector("#promotion-artist");
  const promotionKey = document.querySelector("#promotion-key");
  const promotionCapo = document.querySelector("#promotion-capo");
  const promotionSection = document.querySelector("#promotion-section");
  const measureBoundaries = document.querySelector("#measure-boundaries");
  const addPlayheadBoundaryButton = document.querySelector("#add-playhead-boundary");
  const confirmMeter = document.querySelector("#confirm-meter");
  const confirmGuitarSetup = document.querySelector("#confirm-guitar-setup");
  const guitarDecisions = document.querySelector("#guitar-decisions");
  const previewPromotionButton = document.querySelector("#preview-promotion");
  const promotionStatus = document.querySelector("#promotion-status");
  const promotionPreviewPanel = document.querySelector("#promotion-preview");
  const promotionIssues = document.querySelector("#promotion-issues");
  const promotionPreviewFormat = document.querySelector("#promotion-preview-format");
  const promotionChart = document.querySelector("#promotion-chart");
  const approvePromotionButton = document.querySelector("#approve-promotion");
  const promotionApprovedPanel = document.querySelector("#promotion-approved");
  const promotionExportFormat = document.querySelector("#promotion-export-format");
  const promotionExport = document.querySelector("#promotion-export");
  const promotionRevokeReason = document.querySelector("#promotion-revoke-reason");
  const revokePromotionButton = document.querySelector("#revoke-promotion");
  const practicePhase = document.querySelector("#practice-phase");
  const practiceCreateButton = document.querySelector("#practice-create");
  const practiceControls = document.querySelector("#practice-controls");
  const practiceTarget = document.querySelector("#practice-target");
  const practiceCustomRange = document.querySelector("#practice-custom-range");
  const practiceCustomStart = document.querySelector("#practice-custom-start");
  const practiceCustomEnd = document.querySelector("#practice-custom-end");
  const practiceSpeed = document.querySelector("#practice-speed");
  const practiceCountIn = document.querySelector("#practice-count-in");
  const practiceLoop = document.querySelector("#practice-loop");
  const practiceSaveButton = document.querySelector("#practice-save");
  const practiceStartButton = document.querySelector("#practice-start");
  const practicePauseButton = document.querySelector("#practice-pause");
  const practiceClearButton = document.querySelector("#practice-clear");
  const practiceSourceState = document.querySelector("#practice-source-state");
  const practiceStatus = document.querySelector("#practice-status");
  const practiceUsageSessions = document.querySelector("#practice-usage-sessions");
  const practiceUsageHeads = document.querySelector("#practice-usage-heads");
  const practiceUsageAttempts = document.querySelector("#practice-usage-attempts");
  const practiceUsageReceipts = document.querySelector("#practice-usage-receipts");
  const practiceUsageRecovery = document.querySelector("#practice-usage-recovery");
  const practiceUsageBytes = document.querySelector("#practice-usage-bytes");
  const practiceStorageStatus = document.querySelector("#practice-storage-status");
  const practiceHistoryClearButton = document.querySelector("#practice-history-clear");
  const practiceHistoryDialog = document.querySelector("#practice-history-dialog");
  const practiceHistoryForm = document.querySelector("#practice-history-form");
  const practiceHistoryCounts = document.querySelector("#practice-history-dialog-counts");
  const practiceHistoryConfirmation = document.querySelector(
    "#practice-history-confirmation"
  );
  const practiceHistoryDialogStatus = document.querySelector(
    "#practice-history-dialog-status"
  );
  const practiceHistoryCancelButton = document.querySelector(
    "#practice-history-cancel"
  );
  const practiceHistorySubmitButton = document.querySelector(
    "#practice-history-submit"
  );
  const shutdownButton = document.querySelector("#shutdown");

  let sources = [];
  let acquisitions = [];
  let currentAcquisition = null;
  let acquisitionPoll = null;
  let pendingAcquisitionRequest = null;
  let active = null;
  let waveform = null;
  let sampleRate = 1;
  let durationFrames = 1;
  let loopRange = null;
  let practiceRange = null;
  let dragStartFrame = null;
  let animationFrame = null;
  let loopPlaybackRequested = false;
  let analysisRuns = [];
  let currentAnalysisRun = null;
  let currentTimeline = null;
  let analysisPoll = null;
  let reviewSessions = [];
  let currentReview = null;
  let selectedReviewSegmentId = null;
  let pendingReviewCreateKey = null;
  let pendingConflict = null;
  let currentPromotionPreview = null;
  let currentPromotionConfig = null;
  let currentPromotionReviewToken = null;
  let currentApproval = null;
  let currentPromotionExports = null;
  let practiceSessions = [];
  let currentPractice = null;
  let practiceCountInEpoch = 0;
  let practiceAudioContext = null;
  let practiceHistoryUsage = null;
  let practiceHistoryResetKey = null;
  let practiceHistoryResetPending = false;
  let practiceHistoryResetUnresolved = false;
  let practiceStartPending = false;
  let practiceSaveAllowed = true;
  let practiceSaveBlocking = [];
  promotionStatus.tabIndex = -1;

  const setStatus = (message, kind = "neutral") => {
    status.textContent = message;
    status.dataset.kind = kind;
  };

  const request = async (url, options = {}) => {
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
    });
    const payload = await response.json();
    if (!response.ok) {
      const error = new Error(payload.error?.message || "The local request failed.");
      error.code = payload.error?.code || "request_failed";
      error.durableResolution = true;
      throw error;
    }
    return payload;
  };

  const bootstrap = async () => {
    const token = window.location.hash.slice(1);
    if (token) {
      try {
        await request("/api/session", {
          method: "POST",
          headers: {"X-ChordAtlas-Bootstrap": token},
        });
        history.replaceState(null, "", "/");
      } catch {
        // A refresh may repeat an expired bootstrap while its session cookie is valid.
      }
    }
    try {
      await loadSources();
      await loadAcquisitions();
      await loadPracticeUsage();
      setStatus(
        token ? "Local service connected. Choose a WAV file." : "Local session restored.",
        "success"
      );
    } catch (error) {
      setStatus(
        token ? error.message : "Open Studio with the fresh launch URL printed by chordatlas-studio.",
        "error"
      );
    }
  };

  const updateImportState = () => {
    importButton.disabled = !(fileInput.files.length === 1 && authorization.checked);
  };

  const resetFileAuthorization = () => {
    authorization.checked = false;
    importButton.disabled = true;
  };

  const importAudio = async () => {
    const file = fileInput.files[0];
    if (!file || !authorization.checked) return;
    importButton.disabled = true;
    setStatus("Importing, validating, and building waveform peaks…");
    try {
      const value = await request("/api/import", {
        method: "POST",
        headers: {
          "Content-Type": file.type === "audio/wav" ? "audio/wav" : "application/octet-stream",
          "X-ChordAtlas-Authorized": "true",
          "X-ChordAtlas-File-Name": encodeURIComponent(file.name),
        },
        body: file,
      });
      setStatus(`${value.source.display_name} imported into private project storage.`, "success");
      fileInput.value = "";
      authorization.checked = false;
      await loadSources(value.source.id);
      fileInput.focus();
    } catch (error) {
      setStatus(error.message, "error");
      fileInput.focus();
    }
    fileInput.value = "";
    resetFileAuthorization();
    updateImportState();
  };

  const updateAcquisitionInput = () => {
    const candidate = remoteUrl.value.trim();
    acquireButton.disabled = !(
      remoteName.value.trim()
      && candidate.startsWith("https://")
      && remoteAuthorization.checked
    );
  };

  const resetRemoteAuthorization = () => {
    remoteAuthorization.checked = false;
    updateAcquisitionInput();
  };

  const setAcquisitionStatus = (message, kind = "neutral") => {
    acquisitionStatus.textContent = message;
    acquisitionStatus.dataset.kind = kind;
  };

  const acquisitionRequestKey = (url, displayName, authorizationConfirmed) => {
    const unchanged = pendingAcquisitionRequest
      && pendingAcquisitionRequest.url === url
      && pendingAcquisitionRequest.displayName === displayName
      && pendingAcquisitionRequest.authorizationConfirmed === authorizationConfirmed;
    if (!unchanged) {
      pendingAcquisitionRequest = {
        url,
        displayName,
        authorizationConfirmed,
        key: `acquire-${crypto.randomUUID()}`,
      };
    }
    return pendingAcquisitionRequest.key;
  };

  const loadAcquisitions = async (preferredId = null) => {
    clearTimeout(acquisitionPoll);
    const value = await request("/api/acquisitions");
    acquisitions = value.acquisitions;
    if (!acquisitions.length) {
      currentAcquisition = null;
      renderAcquisition(null);
      return;
    }
    const selected = acquisitions.find((item) => item.run_id === preferredId)
      || acquisitions.at(-1);
    currentAcquisition = selected;
    renderAcquisition(selected);
    if (
      selected.status === "succeeded"
      && sources.some((item) => item.source.id === selected.source_id)
    ) {
      sourceSelect.value = selected.source_id;
      await loadSource(selected.source_id);
    }
    if (["queued", "running", "cancel_requested"].includes(selected.status)) {
      acquisitionPoll = setTimeout(() => selectAcquisition(selected.run_id), 350);
    }
  };

  const selectAcquisition = async (runId) => {
    clearTimeout(acquisitionPoll);
    const value = await request(`/api/acquisitions/${runId}`);
    currentAcquisition = value.acquisition;
    renderAcquisition(currentAcquisition);
    if (["queued", "running", "cancel_requested"].includes(currentAcquisition.status)) {
      acquisitionPoll = setTimeout(() => selectAcquisition(runId), 350);
      return;
    }
    if (currentAcquisition.status === "succeeded") {
      await loadSources(currentAcquisition.source_id);
      document.querySelector("#workspace-title").tabIndex = -1;
      document.querySelector("#workspace-title").focus();
    } else {
      acquisitionStatus.focus();
    }
  };

  const renderAcquisition = (run) => {
    const activeRun = run && ["queued", "running", "cancel_requested"].includes(run.status);
    acquisitionProgress.hidden = !activeRun;
    acquisitionProgress.removeAttribute("value");
    acquisitionProgress.removeAttribute("max");
    if (activeRun && Number.isInteger(run.byte_length) && run.byte_length > 0) {
      acquisitionProgress.max = run.byte_length;
      acquisitionProgress.value = run.bytes_received;
    }
    cancelAcquisitionButton.hidden = !activeRun || !run.cancellable;
    retryAcquisitionButton.hidden = !run || !(
      run.locator_retained
      && (run.status === "cancelled" || (run.status === "failed" && run.retryable))
    );
    forgetAcquisitionButton.hidden = !run || !run.locator_retained || activeRun;
    acquisitionDetails.hidden = !run;
    acquisitionMetadata.replaceChildren();
    if (!run) {
      setAcquisitionStatus("No remote acquisition started.");
      return;
    }
    const progressText = run.byte_length
      ? ` ${run.bytes_received.toLocaleString()} of ${run.byte_length.toLocaleString()} bytes.`
      : "";
    const message = {
      queued: `Authorized acquisition queued for ${run.display_name}.`,
      running: run.cancellable
        ? `${run.display_name}: ${run.phase}.${progressText}`
        : `${run.display_name}: finalizing; cancellation is no longer available.`,
      cancel_requested: `Cancellation requested for ${run.display_name}; waiting for a safe boundary.`,
      cancelled: `Acquisition cancelled. Temporary bytes were removed; existing sources are unchanged.`,
      failed: run.failure_message || "Acquisition failed safely. No source was saved.",
      succeeded: `${run.display_name} is ready in the waveform workspace.`,
    }[run.status];
    setAcquisitionStatus(
      message,
      run.status === "failed" ? "error" : run.status === "succeeded" ? "success" : "neutral"
    );
    cancelAcquisitionButton.setAttribute(
      "aria-label",
      `Cancel acquisition for ${run.display_name}`
    );
    retryAcquisitionButton.setAttribute(
      "aria-label",
      `Retry acquisition for ${run.display_name}`
    );
    forgetAcquisitionButton.setAttribute(
      "aria-label",
      `Forget private locator for ${run.display_name}`
    );
    for (const [term, description] of [
      ["Attempt", run.run_id],
      ["Status", run.status],
      ["Phase", run.phase],
      ["Failure code", run.failure_code || "—"],
      ["Retryable", run.retryable && run.locator_retained ? "Yes" : "No"],
      ["Updated", run.updated_at],
    ]) {
      const wrapper = document.createElement("div");
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = term;
      dd.textContent = description;
      wrapper.append(dt, dd);
      acquisitionMetadata.append(wrapper);
    }
  };

  const startAcquisition = async () => {
    if (acquireButton.disabled) return;
    acquireButton.disabled = true;
    setAcquisitionStatus("Recording authorization before contacting the source…");
    const displayName = remoteName.value.trim();
    const url = remoteUrl.value.trim();
    const authorizationConfirmed = remoteAuthorization.checked;
    const idempotencyKey = acquisitionRequestKey(
      url,
      displayName,
      authorizationConfirmed
    );
    try {
      const value = await request("/api/acquisitions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({
          url,
          display_name: displayName,
          authorization_confirmed: authorizationConfirmed,
        }),
      });
      pendingAcquisitionRequest = null;
      remoteUrl.value = "";
      remoteAuthorization.checked = false;
      remoteUrl.type = "password";
      toggleRemoteUrl.textContent = "Show URL";
      toggleRemoteUrl.setAttribute("aria-pressed", "false");
      currentAcquisition = value.acquisition;
      renderAcquisition(currentAcquisition);
      acquisitionPoll = setTimeout(
        () => selectAcquisition(currentAcquisition.run_id),
        200
      );
    } catch (error) {
      if (error.durableResolution) pendingAcquisitionRequest = null;
      setAcquisitionStatus(error.message, "error");
      acquisitionStatus.focus();
    } finally {
      updateAcquisitionInput();
    }
  };

  const cancelAcquisition = async () => {
    if (!currentAcquisition) return;
    try {
      const value = await request(
        `/api/acquisitions/${currentAcquisition.run_id}/cancel`,
        {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: "{}",
        }
      );
      currentAcquisition = value.acquisition;
      renderAcquisition(currentAcquisition);
      acquisitionPoll = setTimeout(
        () => selectAcquisition(currentAcquisition.run_id),
        200
      );
    } catch (error) {
      setAcquisitionStatus(error.message, "error");
      acquisitionStatus.focus();
    }
  };

  const retryAcquisition = async () => {
    if (!currentAcquisition) return;
    retryAcquisitionButton.disabled = true;
    try {
      const value = await request(
        `/api/acquisitions/${currentAcquisition.run_id}/retry`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": `retry-acquisition-${crypto.randomUUID()}`,
          },
          body: "{}",
        }
      );
      currentAcquisition = value.acquisition;
      renderAcquisition(currentAcquisition);
      acquisitionPoll = setTimeout(
        () => selectAcquisition(currentAcquisition.run_id),
        200
      );
    } catch (error) {
      setAcquisitionStatus(error.message, "error");
      acquisitionStatus.focus();
    } finally {
      retryAcquisitionButton.disabled = false;
    }
  };

  const forgetAcquisitionLocator = async () => {
    if (!currentAcquisition?.locator_retained) return;
    if (!window.confirm(
      "Remove this private URL? Attempt history and imported media stay available, but retry is disabled."
    )) return;
    try {
      const value = await request(
        `/api/acquisitions/${currentAcquisition.run_id}/forget`,
        {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: "{}",
        }
      );
      currentAcquisition = value.acquisition;
      renderAcquisition(currentAcquisition);
      setAcquisitionStatus(
        "Private locator removed. Attempt history and imported media were preserved.",
        "success"
      );
      acquisitionStatus.focus();
    } catch (error) {
      setAcquisitionStatus(error.message, "error");
      acquisitionStatus.focus();
    }
  };

  const loadSources = async (preferredId = null) => {
    const value = await request("/api/sources");
    sources = value.sources;
    sourceSelect.replaceChildren();
    if (!sources.length) {
      const option = document.createElement("option");
      option.textContent = "No imported audio";
      option.value = "";
      sourceSelect.append(option);
      sourceSelect.disabled = true;
      return;
    }
    for (const item of sources) {
      const option = document.createElement("option");
      option.value = item.source.id;
      option.textContent = `${
        item.source_kind === "direct_https_wav" ? "Direct HTTPS WAV" : "Local WAV"
      } · ${item.source.display_name}`;
      sourceSelect.append(option);
    }
    sourceSelect.disabled = false;
    const selected = preferredId || sources.at(-1).source.id;
    sourceSelect.value = selected;
    await loadSource(selected);
  };

  const loadSource = async (sourceId) => {
    cancelPracticeCountIn();
    currentApproval = null;
    practiceRange = null;
    active = sources.find((item) => item.source.id === sourceId);
    if (!active) return;
    audio.pause();
    audio.src = active.playback_url;
    waveform = await request(active.waveform_url);
    sampleRate = waveform.timebase.sample_rate;
    durationFrames = waveform.timebase.duration_frames;
    seek.max = String(durationFrames);
    seek.value = "0";
    loopStart.max = String(Math.max(durationFrames - 1, 0));
    loopEnd.max = String(durationFrames);
    loopEnd.value = String(durationFrames);
    playButton.disabled = false;
    pauseButton.disabled = false;
    seek.disabled = false;
    loopFieldset.disabled = false;
    restoreLoop(sourceId);
    clearLegacyBrowserLoop(sourceId);
    renderMetadata(active.asset);
    resizeAndDraw();
    updatePosition(0);
    canvas.setAttribute(
      "aria-label",
      `Waveform for ${active.source.display_name}, ${durationFrames} sample frames.`
    );
    analysisScope.disabled = false;
    analyzeButton.disabled = false;
    await loadAnalysisRuns();
    await loadActiveApprovals();
    await loadPracticeSessions();
  };

  const loadActiveApprovals = async () => {
    if (!active) return;
    const value = await request(`/api/sources/${active.source.id}/approvals`);
    currentApproval = value.approvals.at(-1) || null;
  };

  const renderMetadata = (asset) => {
    const values = [
      ["Format", `${asset.codec} / ${asset.container}`],
      ["Sample rate", `${asset.timebase.sample_rate.toLocaleString()} Hz`],
      ["Channels", asset.channels === 1 ? "Mono" : "Stereo"],
      ["Frames", asset.timebase.duration_frames.toLocaleString()],
    ];
    metadata.replaceChildren();
    for (const [term, description] of values) {
      const wrapper = document.createElement("div");
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = term;
      dd.textContent = description;
      wrapper.append(dt, dd);
      metadata.append(wrapper);
    }
  };

  const resizeAndDraw = () => {
    if (!waveform) return;
    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.max(Math.round(rect.width * ratio), 1);
    canvas.height = Math.max(Math.round(rect.height * ratio), 1);
    drawWaveform();
  };

  const drawWaveform = () => {
    const context = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    context.fillStyle = "#101820";
    context.fillRect(0, 0, width, height);
    context.strokeStyle = "#65d1b6";
    context.lineWidth = Math.max(window.devicePixelRatio || 1, 1);
    context.beginPath();
    const middle = height / 2;
    waveform.buckets.forEach((bucket, index) => {
      const x = (index / Math.max(waveform.buckets.length - 1, 1)) * width;
      const yTop = middle - (bucket.max_q15 / 32768) * middle * 0.88;
      const yBottom = middle - (bucket.min_q15 / 32768) * middle * 0.88;
      context.moveTo(x, yTop);
      context.lineTo(x, yBottom);
    });
    context.stroke();
    if (loopRange) {
      const startX = (loopRange.start / durationFrames) * width;
      const endX = (loopRange.end / durationFrames) * width;
      context.fillStyle = "rgba(247, 193, 72, 0.2)";
      context.fillRect(startX, 0, endX - startX, height);
      context.strokeStyle = "#f7c148";
      context.strokeRect(startX, 0, endX - startX, height);
    }
  };

  const frameFromPointer = (event) => {
    const rect = canvas.getBoundingClientRect();
    const fraction = Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1);
    return Math.round(fraction * durationFrames);
  };

  const seekFrame = (frame) => {
    const clamped = Math.min(Math.max(Math.round(frame), 0), durationFrames);
    audio.currentTime = clamped / sampleRate;
    seek.value = String(clamped);
    updatePosition(clamped);
  };

  const updatePosition = (frame) => {
    const clamped = Math.min(Math.max(Math.round(frame), 0), durationFrames);
    const seconds = clamped / sampleRate;
    position.textContent = `${clamped.toLocaleString()} frames · ${formatTime(seconds)}`;
    seek.value = String(clamped);
    cursor.style.left = `${(clamped / durationFrames) * 100}%`;
    highlightCandidate(clamped);
    highlightReview(clamped);
  };

  const tick = () => {
    const frame = enforceLoopBoundary();
    updatePosition(frame);
    if (!audio.paused) {
      animationFrame = requestAnimationFrame(tick);
    }
  };

  const play = async () => {
    try {
      if (loopRange) {
        const frame = Math.round(audio.currentTime * sampleRate);
        if (frame < loopRange.start || frame >= loopRange.end) {
          seekFrame(loopRange.start);
        }
      }
      await audio.play();
      loopPlaybackRequested = true;
      cancelAnimationFrame(animationFrame);
      tick();
    } catch {
      loopPlaybackRequested = false;
      setStatus("The browser could not start audio playback. Try pressing Play again.", "error");
    }
  };

  const pause = () => {
    loopPlaybackRequested = false;
    audio.pause();
    cancelAnimationFrame(animationFrame);
    updatePosition(Math.round(audio.currentTime * sampleRate));
  };

  const enforceLoopBoundary = () => {
    let frame = Math.round(audio.currentTime * sampleRate);
    if (
      loopRange
      && loopPlaybackRequested
      && frame >= loopRange.end
    ) {
      frame = loopRange.start;
      audio.currentTime = frame / sampleRate;
    } else if (
      practiceRange
      && loopPlaybackRequested
      && frame >= practiceRange.end
    ) {
      frame = practiceRange.end;
      audio.pause();
      if (Math.round(audio.currentTime * sampleRate) !== frame) {
        audio.currentTime = frame / sampleRate;
      }
      loopPlaybackRequested = false;
      practicePhase.textContent = "Paused · range complete";
      practiceStatus.textContent =
        "The selected practice range reached its exclusive end.";
    }
    return frame;
  };

  const restartLoopAtMediaEnd = async () => {
    if (!loopRange || !loopPlaybackRequested) {
      loopPlaybackRequested = false;
      updatePosition(durationFrames);
      return;
    }
    seekFrame(loopRange.start);
    loopPlaybackRequested = false;
    try {
      await audio.play();
      loopPlaybackRequested = true;
      tick();
    } catch {
      loopPlaybackRequested = false;
      setStatus("Playback stopped at the media boundary. Press Play to resume the loop.", "error");
    }
  };

  const setLoop = (start, end) => {
    const normalizedStart = Math.max(0, Math.min(Math.round(start), durationFrames - 1));
    const normalizedEnd = Math.max(1, Math.min(Math.round(end), durationFrames));
    if (normalizedEnd <= normalizedStart) {
      setStatus("Loop end must be greater than loop start.", "error");
      return;
    }
    loopRange = {start: normalizedStart, end: normalizedEnd};
    loopStart.value = String(normalizedStart);
    loopEnd.value = String(normalizedEnd);
    loopState.textContent = `Loop ${normalizedStart.toLocaleString()}–${normalizedEnd.toLocaleString()}`;
    drawWaveform();
  };

  const clearLoop = () => {
    loopRange = null;
    loopState.textContent = "Loop off";
    drawWaveform();
  };

  const restoreLoop = (_sourceId) => {
    loopRange = null;
    loopState.textContent = "Loop off";
  };

  const clearLegacyBrowserLoop = (sourceId) => {
    try {
      window.localStorage.removeItem(`chordatlas.loop.${sourceId}`);
    } catch {
      // Browser storage is never authoritative; cleanup failure is harmless.
    }
  };

  const setAnalysisStatus = (message, kind = "neutral") => {
    analysisStatus.textContent = message;
    analysisStatus.dataset.kind = kind;
  };

  const loadAnalysisRuns = async (preferredId = null) => {
    clearTimeout(analysisPoll);
    currentTimeline = null;
    candidateLane.replaceChildren();
    clearReview();
    analysisSummary.hidden = true;
    const value = await request(`/api/sources/${active.source.id}/analysis-runs`);
    analysisRuns = value.runs;
    analysisRunSelect.replaceChildren();
    if (!analysisRuns.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No analysis runs";
      analysisRunSelect.append(option);
      analysisRunSelect.disabled = true;
      currentAnalysisRun = null;
      renderAnalysisState(null);
      return;
    }
    for (const run of analysisRuns) {
      const option = document.createElement("option");
      option.value = run.run_id;
      option.textContent = `${run.created_at} · ${run.status}`;
      analysisRunSelect.append(option);
    }
    analysisRunSelect.disabled = false;
    const selected = preferredId || analysisRuns.at(-1).run_id;
    analysisRunSelect.value = selected;
    await selectAnalysisRun(selected);
  };

  const selectAnalysisRun = async (runId) => {
    clearTimeout(analysisPoll);
    if (currentAnalysisRun?.run_id !== runId) {
      pendingReviewCreateKey = null;
    }
    const value = await request(`/api/analysis-runs/${runId}`);
    currentAnalysisRun = value.run;
    renderAnalysisState(currentAnalysisRun);
    if (currentAnalysisRun.status === "succeeded") {
      const timelineValue = await request(`/api/analysis-runs/${runId}/timeline`);
      currentTimeline = timelineValue.timeline;
      renderTimeline(currentTimeline);
      await loadReviewSessions(runId);
    } else {
      currentTimeline = null;
      candidateLane.replaceChildren();
      analysisSummary.hidden = true;
      clearReview();
    }
    if (["queued", "running", "cancel_requested"].includes(currentAnalysisRun.status)) {
      analysisPoll = setTimeout(() => selectAnalysisRun(runId), 350);
    }
  };

  const renderAnalysisState = (run) => {
    const activeState = run && ["queued", "running", "cancel_requested"].includes(run.status);
    analysisProgress.hidden = !activeState;
    cancelAnalysisButton.hidden = !activeState;
    retryAnalysisButton.hidden = !run
      || (run.status !== "cancelled" && !(run.status === "failed" && run.retryable));
    analyzeButton.disabled = !active || Boolean(activeState);
    analysisMetadata.replaceChildren();
    if (!run) {
      setAnalysisStatus("No machine draft yet. Choose a scope and analyze explicitly.");
      return;
    }
    const message = {
      queued: "Analysis queued locally.",
      running: "Preparing and analyzing authorized audio locally…",
      cancel_requested: "Cancellation requested; waiting for the worker to stop safely…",
      cancelled: "Analysis cancelled. The source and prior runs are unchanged.",
      failed: run.failure_message || "Analysis failed safely. Retry as a new run.",
      succeeded: "Machine draft ready. Every proposal remains unreviewed.",
    }[run.status];
    setAnalysisStatus(message, run.status === "failed" ? "error" : run.status === "succeeded" ? "success" : "neutral");
    const details = [
      ["Run", run.run_id],
      ["Status", run.status],
      ["Engine", `${run.engine.engine_id} ${run.engine.engine_version}`],
      ["Model", `${run.engine.model_name} ${run.engine.model_version}`],
      ["Vocabulary", `${run.engine.vocabulary_id} ${run.engine.vocabulary_version}`],
      ["Scope", `${run.scope.start_frame}–${run.scope.end_frame} frames`],
    ];
    for (const [term, description] of details) {
      const wrapper = document.createElement("div");
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = term;
      dd.textContent = description;
      wrapper.append(dt, dd);
      analysisMetadata.append(wrapper);
    }
  };

  const startAnalysis = async () => {
    if (!active) return;
    let range = null;
    if (analysisScope.value === "loop") {
      if (!loopRange) {
        setAnalysisStatus("Set a valid loop before analyzing the current loop.", "error");
        return;
      }
      range = {start_frame: loopRange.start, end_frame: loopRange.end};
    }
    analyzeButton.disabled = true;
    setAnalysisStatus("Creating a new immutable analysis run…");
    try {
      const value = await request(`/api/sources/${active.source.id}/analysis-runs`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({range}),
      });
      await loadAnalysisRuns(value.run.run_id);
    } catch (error) {
      setAnalysisStatus(error.message, "error");
      analyzeButton.disabled = false;
    }
  };

  const cancelAnalysis = async () => {
    if (!currentAnalysisRun) return;
    try {
      const value = await request(`/api/analysis-runs/${currentAnalysisRun.run_id}/cancel`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: "{}",
      });
      currentAnalysisRun = value.run;
      renderAnalysisState(currentAnalysisRun);
      analysisPoll = setTimeout(() => selectAnalysisRun(currentAnalysisRun.run_id), 200);
    } catch (error) {
      setAnalysisStatus(error.message, "error");
    }
  };

  const retryAnalysis = async () => {
    if (!currentAnalysisRun) return;
    retryAnalysisButton.disabled = true;
    setAnalysisStatus("Retrying the selected immutable analysis request…");
    try {
      const value = await request(`/api/analysis-runs/${currentAnalysisRun.run_id}/retry`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: "{}",
      });
      await loadAnalysisRuns(value.run.run_id);
    } catch (error) {
      setAnalysisStatus(error.message, "error");
    } finally {
      retryAnalysisButton.disabled = false;
    }
  };

  const renderTimeline = (timeline) => {
    candidateLane.replaceChildren();
    const tempo = timeline.tempo_hypotheses[0];
    const key = timeline.key_hypotheses[0];
    analysisSummary.hidden = false;
    analysisSummary.textContent = [
      tempo ? `Tempo hypothesis ${(tempo.bpm_milli / 1000).toFixed(1)} BPM` : "Tempo unresolved",
      key ? `Key hypothesis ${key.label} (${formatConfidence(key.confidence_ppm)})` : "Key unresolved",
      `${timeline.beats.length} beat proposals`,
      `${timeline.segments.length} chord segments`,
    ].join(" · ");
    if (timeline.result_kind === "no_candidates") {
      const item = document.createElement("li");
      item.className = "candidate-card";
      item.textContent = "No candidates produced. This is a successful empty hypothesis.";
      candidateLane.append(item);
      return;
    }
    for (const segment of timeline.segments) {
      const item = document.createElement("li");
      item.className = "candidate-card";
      item.dataset.startFrame = String(segment.range.start_frame);
      item.dataset.endFrame = String(segment.range.end_frame);
      const primary = segment.candidates[0];
      const symbol = primary?.canonical_symbol || (segment.state === "unknown" ? "Unknown" : "N.C.");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "candidate-seek";
      button.setAttribute(
        "aria-label",
        `${symbol}, machine proposal, ${primary ? formatConfidence(primary.confidence_ppm) : "unscored"}, ` +
          `${formatTime(segment.range.start_frame / sampleRate)} to ` +
          `${formatTime(segment.range.end_frame / sampleRate)}, ` +
          `${Math.max(segment.candidates.length - 1, 0)} alternatives`
      );
      const name = document.createElement("span");
      name.className = "candidate-symbol";
      name.textContent = symbol;
      const detail = document.createElement("span");
      detail.className = "candidate-detail";
      detail.textContent = `${segment.range.start_frame.toLocaleString()}–` +
        `${segment.range.end_frame.toLocaleString()} · ` +
        `${primary ? formatConfidence(primary.confidence_ppm) : "unscored"} · proposal`;
      button.append(name, detail);
      button.addEventListener("click", () => seekFrame(segment.range.start_frame));
      item.append(button);
      if (segment.candidates.length > 1) {
        const alternatives = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = `${segment.candidates.length - 1} alternatives`;
        const list = document.createElement("ol");
        for (const candidate of segment.candidates.slice(1)) {
          const alternative = document.createElement("li");
          alternative.textContent = `${candidate.raw_label} · ${formatConfidence(candidate.confidence_ppm)}`;
          list.append(alternative);
        }
        alternatives.append(summary, list);
        item.append(alternatives);
      }
      candidateLane.append(item);
    }
    highlightCandidate(Math.round(audio.currentTime * sampleRate));
  };

  const highlightCandidate = (frame) => {
    for (const item of candidateLane.querySelectorAll(".candidate-card")) {
      const activeCandidate =
        Number(item.dataset.startFrame) <= frame && frame < Number(item.dataset.endFrame);
      if (activeCandidate) {
        item.setAttribute("aria-current", "true");
      } else {
        item.removeAttribute("aria-current");
      }
    }
  };

  const setReviewStatus = (message, kind = "neutral") => {
    reviewStatus.textContent = message;
    reviewStatus.dataset.kind = kind;
    reviewStatus.tabIndex = -1;
  };

  const clearReview = () => {
    reviewSessions = [];
    currentReview = null;
    selectedReviewSegmentId = null;
    reviewSessionSelect.replaceChildren();
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No review sessions";
    reviewSessionSelect.append(option);
    reviewSessionSelect.disabled = true;
    startReviewButton.disabled = !currentAnalysisRun || currentAnalysisRun.status !== "succeeded";
    undoReviewButton.disabled = true;
    redoReviewButton.disabled = true;
    resetReviewButton.disabled = true;
    acceptBoundariesButton.disabled = true;
    finishReviewButton.disabled = true;
    noChordControls.disabled = true;
    sectionControls.disabled = true;
    reviewPhase.textContent = "Not started";
    reviewMetrics.hidden = true;
    reviewConflictActions.hidden = true;
    finishReason.textContent = "";
    reviewLane.replaceChildren();
    sectionList.replaceChildren();
    setReviewStatus(
      currentAnalysisRun?.status === "succeeded"
        ? "Start a private review or restore a saved one."
        : "Complete a local analysis to start a durable review."
    );
  };

  const loadReviewSessions = async (runId, preferredId = null) => {
    const value = await request(`/api/analysis-runs/${runId}/review-sessions`);
    reviewSessions = value.sessions;
    reviewSessionSelect.replaceChildren();
    startReviewButton.disabled = false;
    if (!reviewSessions.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No review sessions";
      reviewSessionSelect.append(option);
      reviewSessionSelect.disabled = true;
      setReviewStatus("No review exists for this immutable machine draft.");
      return;
    }
    for (const session of reviewSessions) {
      const option = document.createElement("option");
      option.value = session.session_id;
      option.textContent = `${session.created_at} · saved review`;
      reviewSessionSelect.append(option);
    }
    reviewSessionSelect.disabled = false;
    const selected = preferredId || reviewSessions.at(-1).session_id;
    reviewSessionSelect.value = selected;
    await loadReview(selected);
  };

  const startReview = async () => {
    if (!currentAnalysisRun || currentAnalysisRun.status !== "succeeded") return;
    startReviewButton.disabled = true;
    pendingReviewCreateKey ||= crypto.randomUUID();
    setReviewStatus("Creating a durable review bound to this machine draft…");
    try {
      const value = await request(
        `/api/analysis-runs/${currentAnalysisRun.run_id}/review-sessions`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": pendingReviewCreateKey,
          },
          body: "{}",
        }
      );
      await loadReviewSessions(currentAnalysisRun.run_id, value.review.session.session_id);
      pendingReviewCreateKey = null;
      setReviewStatus("Review created in private project storage.", "success");
    } catch (error) {
      setReviewStatus(error.message, "error");
    } finally {
      startReviewButton.disabled = false;
    }
  };

  const loadReview = async (sessionId, {announce = true} = {}) => {
    try {
      const value = await request(`/api/review-sessions/${sessionId}`);
      currentReview = value.review;
      renderReview();
      if (announce) {
        setReviewStatus("Saved review restored. The raw machine lane remains unchanged.", "success");
      }
    } catch (error) {
      setReviewStatus(error.message, "error");
    }
  };

  const reviewMutation = async (suffix, body, {idempotencyKey = null} = {}) => {
    if (!currentReview) return;
    pause();
    const sessionId = currentReview.session.session_id;
    const selected = selectedReviewSegmentId;
    const focusKey = document.activeElement?.dataset?.focusKey || null;
    const requestKey = idempotencyKey || crypto.randomUUID();
    setReviewStatus("Saving one immutable review revision…");
    try {
      const value = await request(`/api/review-sessions/${sessionId}/${suffix}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${currentReview.head.token}"`,
          "Idempotency-Key": requestKey,
        },
        body: JSON.stringify(body),
      });
      currentReview = value.review;
      invalidatePractice(
        "The saved review changed. Existing practice remains pinned to its prior approval."
      );
      if (selected && currentReview.timeline.segments.some((item) => item.id === selected)) {
        selectedReviewSegmentId = selected;
      } else {
        selectedReviewSegmentId = null;
      }
      renderReview();
      restoreReviewFocus(focusKey);
      pendingConflict = null;
      reviewConflictActions.hidden = true;
      setReviewStatus("Saved locally as a replayable revision.", "success");
    } catch (error) {
      if (error.code === "review_precondition_failed") {
        pendingConflict = {suffix, body, idempotencyKey: requestKey, selected, focusKey};
        await loadReview(sessionId, {announce: false});
        selectedReviewSegmentId = selected;
        renderReview();
        restoreReviewFocus(focusKey);
        reviewConflictActions.hidden = false;
        setReviewStatus(
          "Your edit was rejected because this review changed elsewhere. " +
          "The latest saved revision is loaded; retry or discard your edit.",
          "error"
        );
      } else {
        setReviewStatus(error.message, "error");
      }
    }
  };

  const applyReviewEdit = (kind, parameters) =>
    reviewMutation("edits", {kind, parameters});

  const renderReview = () => {
    if (!currentReview) return;
    if (
      currentPromotionReviewToken
      && currentPromotionReviewToken !== currentReview.head.token
    ) {
      invalidatePromotionPreview();
    }
    const timeline = currentReview.timeline;
    const summary = timeline.summary;
    reviewPhase.textContent = timeline.phase.replaceAll("_", " ");
    undoReviewButton.disabled = !currentReview.head.can_undo;
    redoReviewButton.disabled = !currentReview.head.can_redo;
    const ready = timeline.phase === "ready_for_approval";
    resetReviewButton.disabled = ready;
    acceptBoundariesButton.disabled = ready || summary.machine_boundaries === 0;
    finishReviewButton.disabled = summary.unresolved_segments > 0
      || summary.unreviewed_segments > 0
      || summary.machine_boundaries > 0
      || ready;
    finishReason.textContent = ready
      ? "This revision is ready for approval. Select Undo to resume correction."
      : `${summary.unreviewed_segments} labels and ${summary.machine_boundaries} boundaries ` +
        `still need explicit review; ${summary.unresolved_segments} labels are unresolved.`;
    noChordControls.disabled = ready;
    sectionControls.disabled = ready;
    noChordStart.max = String(timeline.analyzed_range.end_frame - 1);
    noChordEnd.max = String(timeline.analyzed_range.end_frame);
    noChordEnd.value = String(timeline.analyzed_range.end_frame);
    sectionFrame.max = String(timeline.analyzed_range.end_frame - 1);
    reviewMetrics.hidden = false;
    const measure = currentReview.local_measurements;
    reviewMetrics.textContent =
      `${summary.reviewed_segments} reviewed · ${summary.unreviewed_segments} unreviewed · ` +
      `${summary.unresolved_segments} unresolved · ${summary.reviewed_boundaries} reviewed boundaries · ` +
      `${summary.machine_boundaries} machine boundaries · ${measure.accepted_edit_events} saved edits · ` +
      `${measure.session_wall_elapsed_ms} ms wall time (includes idle; not musical quality or effort)`;
    renderSections(timeline.section_markers, {disabled: ready});
    renderReviewSegments(timeline.segments, {disabled: ready});
    renderPromotionSetup();
  };

  const builtInShapes = new Set([
    "A", "Am", "A7", "Bm", "B7", "C", "Cadd9", "Cmaj7", "D", "Dm", "D7",
    "D/F#", "Em", "Em7", "E", "E7", "F", "Fmaj7", "G", "G/B", "G7",
  ]);

  const normalizedPromotionLabel = (segment) => {
    if (segment.state === "no_chord") return "N.C.";
    const match = segment.label?.match(/^([A-G](?:#|b)?):(maj|min)(\/[A-G](?:#|b)?)?$/);
    if (!match) return segment.label;
    return `${match[1]}${match[2] === "min" ? "m" : ""}${match[3] || ""}`;
  };

  const renderPromotionSetup = () => {
    const ready = currentReview?.timeline.phase === "ready_for_approval";
    promotionControls.disabled = !ready;
    promotionPhase.textContent = ready ? "Ready to map" : "Waiting for review";
    if (!ready) {
      promotionStatus.textContent = "Finish review before creating a promotion draft.";
      return;
    }
    promotionStatus.textContent =
      "Confirm the chart identity, full frame grid, and every guitar decision.";
    const range = currentReview.timeline.analyzed_range;
    if (!measureBoundaries.value) {
      measureBoundaries.value = `${range.start_frame}, ${range.end_frame}`;
    }
    const labels = Array.from(new Set(
      currentReview.timeline.segments
        .map(normalizedPromotionLabel)
        .filter((label) => label && label !== "N.C.")
    )).sort();
    const signature = labels.join("\u001f");
    if (guitarDecisions.dataset.signature === signature) return;
    guitarDecisions.dataset.signature = signature;
    guitarDecisions.replaceChildren();
    for (const chord of labels) {
      const row = document.createElement("div");
      row.className = "guitar-decision";
      row.dataset.chord = chord;
      const name = document.createElement("strong");
      name.textContent = chord;

      const voicingLabel = document.createElement("label");
      voicingLabel.textContent = "Voicing";
      const voicing = document.createElement("select");
      voicing.dataset.field = "voicing";
      const voicingOptions = builtInShapes.has(chord)
        ? [["built_in", "Use exported built-in shape"]]
        : [["manual_required", "No built-in shape; add manual note"]];
      for (const [value, label] of voicingOptions) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        voicing.append(option);
      }
      voicing.value = voicingOptions[0][0];
      voicingLabel.append(voicing);

      const playabilityLabel = document.createElement("label");
      playabilityLabel.textContent = "Playability";
      const playability = document.createElement("select");
      playability.dataset.field = "playability";
      for (const [value, label] of [
        ["playable", "Playable as reviewed"],
        ["needs_adjustment", "Needs adjustment"],
      ]) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        playability.append(option);
      }
      playabilityLabel.append(playability);

      const noteLabel = document.createElement("label");
      noteLabel.textContent = "Public voicing note";
      const note = document.createElement("input");
      note.type = "text";
      note.maxLength = 256;
      note.dataset.field = "note";
      if (!builtInShapes.has(chord)) note.required = true;
      noteLabel.append(note);

      const inversionLabel = document.createElement("label");
      const inversion = document.createElement("input");
      inversion.type = "checkbox";
      inversion.dataset.field = "inversion";
      inversion.checked = !chord.includes("/");
      inversionLabel.append(
        inversion,
        document.createTextNode(
          chord.includes("/") ? " Confirm slash bass / inversion" : " No inversion to confirm"
        )
      );
      row.append(name, voicingLabel, playabilityLabel, noteLabel, inversionLabel);
      guitarDecisions.append(row);
    }
  };

  const promotionMapping = () => {
    if (!currentReview || currentReview.timeline.phase !== "ready_for_approval") {
      throw new Error("Finish review before promotion.");
    }
    if (!promotionTitleInput.value.trim()) throw new Error("Enter a chart title.");
    if (!confirmMeter.checked) throw new Error("Confirm the complete 4/4 measure grid.");
    if (!confirmGuitarSetup.checked) {
      throw new Error("Confirm Standard tuning and sounding chord notation.");
    }
    const frameTexts = measureBoundaries.value.split(",").map((item) => item.trim());
    if (frameTexts.some((item) => !/^\d+$/.test(item))) {
      throw new Error("Measure boundaries must be comma-separated integer frames.");
    }
    const decisions = Array.from(guitarDecisions.querySelectorAll(".guitar-decision"))
      .map((row) => {
        const voicing = row.querySelector('[data-field="voicing"]').value;
        const note = row.querySelector('[data-field="note"]').value.trim();
        if (voicing === "manual_required" && !note) {
          throw new Error(`${row.dataset.chord} requires a public manual voicing note.`);
        }
        return {
          chord: row.dataset.chord,
          inversion_reviewed: row.querySelector('[data-field="inversion"]').checked,
          voicing,
          playability: row.querySelector('[data-field="playability"]').value,
          note: note || null,
        };
      });
    return {
      mapping_version: "songchart-explicit-grid-v1",
      title: promotionTitleInput.value.trim(),
      artist: promotionArtist.value.trim() || null,
      key: promotionKey.value.trim() || null,
      tuning: "Standard",
      capo: promotionCapo.value.trim(),
      chart_version: "1.0",
      meter_numerator: 4,
      beat_unit: 4,
      measure_boundaries_frames: frameTexts.map(Number),
      pickup_policy: "full_coverage_confirmed",
      default_section_name: promotionSection.value.trim(),
      notation_mode: "sounding",
      diagram_policy: "built_in_standard_only",
      loss_policy: "allow_declared",
      guitar_decisions: decisions,
    };
  };

  const addPlayheadBoundary = () => {
    const values = measureBoundaries.value
      .split(",")
      .map((item) => item.trim())
      .filter((item) => /^\d+$/.test(item))
      .map(Number);
    values.push(Number(seek.value));
    measureBoundaries.value = Array.from(new Set(values))
      .sort((left, right) => left - right)
      .join(", ");
    measureBoundaries.dispatchEvent(new Event("input", {bubbles: true}));
    measureBoundaries.focus();
  };

  const previewPromotion = async () => {
    try {
      currentPromotionConfig = promotionMapping();
      promotionStatus.textContent = "Validating exact review, grid, guitar choices, and renderers…";
      const value = await request(
        `/api/review-sessions/${currentReview.session.session_id}/promotion-preview`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "If-Match": `"${currentReview.head.token}"`,
          },
          body: JSON.stringify({
            revision_id: currentReview.head.revision_id,
            mapping: currentPromotionConfig,
          }),
        }
      );
      currentPromotionPreview = value.preview;
      currentPromotionReviewToken = currentReview.head.token;
      promotionPreviewPanel.hidden = false;
      promotionApprovedPanel.hidden = true;
      promotionIssues.replaceChildren();
      for (const issue of value.preview.issues) {
        const item = document.createElement("li");
        if (issue.severity === "material") {
          const checkbox = document.createElement("input");
          checkbox.type = "checkbox";
          checkbox.dataset.issueId = issue.id;
          checkbox.setAttribute("aria-label", `Acknowledge ${issue.code}`);
          checkbox.addEventListener("change", updatePromotionApprovalState);
          item.append(checkbox);
        }
        const issueFrame = Number.isInteger(issue.details.frame)
          ? issue.details.frame
          : issue.details.start_frame;
        if (Number.isInteger(issueFrame)) {
          const seekIssue = document.createElement("button");
          seekIssue.type = "button";
          seekIssue.textContent = `Seek frame ${issueFrame.toLocaleString()}`;
          seekIssue.addEventListener("click", () => seekFrame(issueFrame));
          item.append(seekIssue);
        }
        const text = document.createElement("span");
        const details = Object.keys(issue.details).length
          ? ` Details: ${JSON.stringify(issue.details)}`
          : "";
        text.textContent =
          `${issue.severity} · ${issue.code}: ${issue.message}${details}`;
        item.append(text);
        promotionIssues.append(item);
      }
      renderPromotionPreview();
      promotionStatus.textContent =
        "Preview validated. Review and acknowledge every material mapping loss.";
      promotionPhase.textContent = "Preview ready";
      updatePromotionApprovalState();
      promotionChart.focus();
    } catch (error) {
      promotionStatus.textContent = error.message;
      promotionStatus.dataset.kind = "error";
      promotionStatus.focus();
    }
  };

  const updatePromotionApprovalState = () => {
    const material = Array.from(
      promotionIssues.querySelectorAll('input[data-issue-id]')
    );
    approvePromotionButton.disabled =
      !currentPromotionPreview || material.some((item) => !item.checked);
  };

  const invalidatePromotionPreview = () => {
    if (!currentPromotionPreview) return;
    currentPromotionPreview = null;
    currentPromotionConfig = null;
    currentPromotionReviewToken = null;
    promotionPreviewPanel.hidden = true;
    approvePromotionButton.disabled = true;
    promotionPhase.textContent = "Mapping changed";
    promotionStatus.textContent =
      "A mapping or guitar choice changed. Build and review a new exact preview.";
  };

  const approvePromotion = async () => {
    if (!currentPromotionPreview || !currentPromotionConfig) return;
    const acknowledged = Array.from(
      promotionIssues.querySelectorAll('input[data-issue-id]:checked')
    ).map((item) => item.dataset.issueId).sort();
    approvePromotionButton.disabled = true;
    try {
      const value = await request(
        `/api/review-sessions/${currentReview.session.session_id}/approvals`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "If-Match": `"${currentReview.head.token}"`,
            "Idempotency-Key": `approve-${crypto.randomUUID()}`,
          },
          body: JSON.stringify({
            revision_id: currentReview.head.revision_id,
            mapping: currentPromotionConfig,
            expected_spec_id: currentPromotionPreview.spec_id,
            expected_result_id: currentPromotionPreview.result_id,
            expected_issue_digest: currentPromotionPreview.issue_digest,
            acknowledged_issue_ids: acknowledged,
          }),
        }
      );
      currentApproval = value.approval;
      practiceCreateButton.disabled = false;
      practicePhase.textContent = "Approval ready";
      practiceStatus.textContent =
        "Prepare a private practice session from this exact approved chart.";
      const exportsValue = await request(
        `/api/approvals/${currentApproval.approval_id}/exports`
      );
      currentPromotionExports = exportsValue.exports;
      promotionApprovedPanel.hidden = false;
      promotionPhase.textContent = "Approved";
      promotionStatus.textContent =
        "Exact revision approved. Deterministic JSON, Markdown, and text exports are ready.";
      renderPromotionExport();
      const approvedHeading = promotionApprovedPanel.querySelector("h3");
      approvedHeading.tabIndex = -1;
      approvedHeading.focus();
    } catch (error) {
      promotionStatus.textContent = error.message;
      promotionStatus.dataset.kind = "error";
      updatePromotionApprovalState();
      promotionStatus.focus();
    }
  };

  const renderPromotionExport = () => {
    promotionExport.textContent =
      currentPromotionExports?.[promotionExportFormat.value] || "";
  };

  const renderPromotionPreview = () => {
    promotionChart.textContent =
      currentPromotionPreview?.renders?.[promotionPreviewFormat.value] || "";
  };

  const revokePromotion = async () => {
    if (!currentApproval || !window.confirm(
      "Revoke this approval for future exports? Previously written files cannot be retracted."
    )) return;
    try {
      const value = await request(
        `/api/approvals/${currentApproval.approval_id}/revoke`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "If-Match": `"${currentApproval.token}"`,
            "Idempotency-Key": `revoke-${crypto.randomUUID()}`,
          },
          body: JSON.stringify({reason: promotionRevokeReason.value}),
        }
      );
      currentApproval = value.approval;
      invalidatePractice(
        "Approval revoked. Saved practice history remains private but cannot be changed."
      );
      revokePromotionButton.disabled = true;
      promotionPhase.textContent = "Revoked";
      promotionStatus.textContent =
        "Approval revoked for future exports. Existing files were not retracted.";
      promotionStatus.focus();
    } catch (error) {
      promotionStatus.textContent = error.message;
      promotionStatus.dataset.kind = "error";
      promotionStatus.focus();
    }
  };

  const usageCategory = (name) => (
    practiceHistoryUsage?.categories?.[name] || {count: 0, bytes: 0}
  );

  const formatUsageCategory = (name) => {
    const category = usageCategory(name);
    const count = Number(category.count || 0).toLocaleString();
    if (Number.isInteger(category.limit)) {
      return `${count} of ${Number(category.limit).toLocaleString()}`;
    }
    return count;
  };

  const practiceSaveLimitMessage = ({pausedWithoutSaving = false} = {}) => {
    const labels = practiceSaveBlocking.map((name) => (
      name === "attempts" ? "saved-attempt" : "saved-action"
    ));
    const subject = labels.length ? labels.join(" and ") : "saved-practice";
    const limit = labels.length === 1 ? "record limit is" : "record limits are";
    const prefix = pausedWithoutSaving ? "Paused without saving. " : "";
    return (
      `${prefix}Practice saving is unavailable: the project ${subject} ${limit} reached. ` +
      "Existing audio can still use the main Play and Pause controls. " +
      "Clear all practice history to create another saved practice action."
    );
  };

  const announcePracticeSaveLimit = ({pausedWithoutSaving = false} = {}) => {
    practiceStatus.textContent = practiceSaveLimitMessage({pausedWithoutSaving});
    practiceStatus.dataset.kind = "warning";
    practiceStatus.focus();
  };

  const renderPracticeUsage = (usage) => {
    practiceHistoryUsage = usage;
    practiceUsageSessions.textContent = formatUsageCategory("sessions");
    practiceUsageHeads.textContent = formatUsageCategory("heads");
    practiceUsageAttempts.textContent = formatUsageCategory("attempts");
    practiceUsageReceipts.textContent = formatUsageCategory("receipts");
    practiceUsageRecovery.textContent = formatUsageCategory("recovery");
    practiceUsageBytes.textContent =
      `${Number(usage.totals?.bytes || 0).toLocaleString()} bytes`;
    const warnings = Array.isArray(usage.warnings) ? usage.warnings : [];
    practiceStorageStatus.textContent = warnings.length
      ? warnings.join(" ")
      : "Practice record-count limits have safe capacity. No cleanup is automatic.";
    practiceStorageStatus.dataset.kind = warnings.length ? "warning" : "success";
    practiceHistoryClearButton.disabled = !usage.reset_allowed;
    const canPrepare = usage.capabilities?.prepare?.allowed !== false;
    const canSave = usage.capabilities?.save?.allowed !== false;
    practiceSaveAllowed = canSave;
    practiceSaveBlocking = Array.isArray(
      usage.capabilities?.save?.blocking_resources
    )
      ? usage.capabilities.save.blocking_resources
      : [];
    if (!canPrepare) practiceCreateButton.disabled = true;
    for (const control of [
      practiceSaveButton,
      practicePauseButton,
      practiceClearButton
    ]) {
      control.disabled = !canSave;
    }
    practiceStartButton.disabled = !practiceSaveAllowed || practiceStartPending;
  };

  const renderPracticeHistoryDialogCounts = () => {
    practiceHistoryCounts.textContent =
      `${usageCategory("sessions").count.toLocaleString()} sessions, ` +
      `${usageCategory("attempts").count.toLocaleString()} attempts, and ` +
      `${usageCategory("receipts").count.toLocaleString()} saved-action receipts ` +
      `will be cleared.`;
  };

  const loadPracticeUsage = async ({announceFailure = true} = {}) => {
    try {
      const value = await request("/api/practice-history");
      renderPracticeUsage(value.practice_history);
      return value.practice_history;
    } catch (error) {
      practiceHistoryUsage = null;
      practiceHistoryClearButton.disabled = true;
      practiceStorageStatus.textContent =
        "Practice history usage is unavailable; no records were changed.";
      practiceStorageStatus.dataset.kind = "error";
      if (announceFailure) practiceStorageStatus.focus();
      if (announceFailure) throw error;
      return null;
    }
  };

  const openPracticeHistoryDialog = async () => {
    cancelPracticeCountIn();
    pause();
    try {
      const usage = await loadPracticeUsage({announceFailure: false});
      if (!usage) throw new Error("Practice history usage is unavailable.");
    } catch {
      practiceStorageStatus.focus();
      return;
    }
    renderPracticeHistoryDialogCounts();
    practiceHistoryConfirmation.value = "";
    practiceHistoryDialogStatus.textContent = "";
    practiceHistorySubmitButton.disabled = true;
    practiceHistorySubmitButton.textContent = "Clear all practice history";
    practiceHistoryResetKey = null;
    practiceHistoryResetPending = false;
    practiceHistoryResetUnresolved = false;
    practiceHistoryDialog.showModal();
    practiceHistoryConfirmation.focus();
  };

  const closePracticeHistoryDialog = () => {
    if (practiceHistoryResetPending || practiceHistoryResetUnresolved) {
      practiceHistoryDialogStatus.textContent =
        "Clear is in progress or its result is unresolved; it cannot be cancelled. " +
        "Use Check result / retry safely.";
      practiceHistorySubmitButton.focus();
      return;
    }
    practiceHistoryResetKey = null;
    practiceHistoryDialog.close();
    practiceHistoryClearButton.focus();
  };

  const clearPracticeHistory = async (event) => {
    event.preventDefault();
    if (practiceHistoryResetPending) return;
    if (
      practiceHistoryConfirmation.value
      !== "CLEAR ALL PRACTICE HISTORY"
      || !practiceHistoryUsage
    ) {
      practiceHistoryDialogStatus.textContent =
        "Type the exact confirmation phrase before clearing history.";
      practiceHistoryConfirmation.focus();
      return;
    }
    practiceHistoryResetPending = true;
    practiceHistorySubmitButton.disabled = true;
    practiceHistoryCancelButton.disabled = true;
    practiceHistoryConfirmation.disabled = true;
    practiceHistoryResetKey ||= `practice-history-reset-${crypto.randomUUID()}`;
    try {
      const value = await request("/api/practice-history/reset", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${practiceHistoryUsage.token}"`,
          "Idempotency-Key": practiceHistoryResetKey,
        },
        body: JSON.stringify({confirmation: practiceHistoryConfirmation.value}),
      });
      const resetResult = value.practice_history_reset;
      renderPracticeUsage(resetResult.usage);
      const currentHistoryCount = ["sessions", "heads", "attempts", "receipts"]
        .reduce(
          (total, name) => total + Number(resetResult.usage.categories[name].count),
          0
        );
      if (resetResult.replayed && currentHistoryCount > 0) {
        practiceHistoryResetKey = null;
        practiceHistoryResetPending = false;
        practiceHistoryResetUnresolved = false;
        practiceHistoryCancelButton.disabled = false;
        practiceHistoryConfirmation.disabled = false;
        practiceHistoryDialog.close();
        await loadPracticeSessions();
        practiceStatus.textContent =
          "The earlier clear completed, but newer practice history now exists. " +
          "No newer records were deleted.";
        practiceStatus.dataset.kind = "warning";
        practiceStatus.focus();
        return;
      }
      cancelPracticeCountIn();
      pause();
      clearLoop();
      practiceSessions = [];
      currentPractice = null;
      practiceRange = null;
      audio.playbackRate = 1;
      practiceSpeed.value = "1000";
      practiceCountIn.value = "off";
      practiceLoop.checked = false;
      practiceControls.disabled = true;
      practiceTarget.replaceChildren();
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "No saved approved practice";
      practiceTarget.append(placeholder);
      practiceCreateButton.disabled = !currentApproval
        || currentApproval.status !== "active";
      practicePhase.textContent = currentApproval ? "Approval ready" : "Waiting for approval";
      practiceStatus.textContent =
        "All project practice history was cleared. Audio, reviews, approvals, " +
        "charts, and exports were kept.";
      practiceStatus.dataset.kind = "success";
      practiceHistoryDialog.close();
      practiceHistoryResetKey = null;
      practiceHistoryResetPending = false;
      practiceHistoryResetUnresolved = false;
      practiceHistoryCancelButton.disabled = false;
      practiceHistoryConfirmation.disabled = false;
      practiceStatus.focus();
    } catch (error) {
      practiceHistoryResetPending = false;
      practiceHistoryCancelButton.disabled = false;
      practiceHistoryConfirmation.disabled = false;
      if (
        error.code === "practice_history_changed"
        || error.code === "practice_conflict"
      ) {
        practiceHistoryResetKey = null;
        const refreshed = await loadPracticeUsage({announceFailure: false});
        if (!refreshed) {
          practiceHistoryResetUnresolved = false;
          practiceHistoryDialogStatus.textContent =
            "Practice history changed, but refreshed usage is unavailable. " +
            "Close this dialog and try again when usage is available.";
          practiceHistorySubmitButton.disabled = true;
          practiceHistoryCancelButton.focus();
          return;
        }
        renderPracticeHistoryDialogCounts();
        practiceHistoryResetUnresolved = false;
        practiceHistoryConfirmation.value = "";
        practiceHistoryDialogStatus.textContent =
          "Practice history changed in another tab. Review the updated counts " +
          "and type the confirmation again.";
        practiceHistoryConfirmation.focus();
      } else if (error.code === "practice_busy") {
        practiceHistoryResetUnresolved = false;
        practiceHistoryDialogStatus.textContent =
          "Practice history is busy in another operation. Wait, then press the " +
          "same button to retry safely.";
        practiceHistorySubmitButton.disabled = false;
      } else {
        practiceHistoryResetUnresolved = true;
        practiceHistoryCancelButton.disabled = true;
        practiceHistoryDialogStatus.textContent =
          `${error.message} Use the same button to check the result or retry safely.`;
        practiceHistorySubmitButton.textContent = "Check result / retry safely";
        practiceHistorySubmitButton.disabled = false;
      }
    }
  };

  const loadPracticeSessions = async () => {
    cancelPracticeCountIn();
    practiceSessions = [];
    currentPractice = null;
    practiceControls.disabled = true;
    practiceCreateButton.disabled = !currentApproval
      || currentApproval.status !== "active";
    practiceTarget.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "No saved approved practice";
    practiceTarget.append(placeholder);
    practiceStatus.textContent = currentApproval
      ? "Active approval restored. Prepare approved practice."
      : "Approve an exact chart or select a source with saved practice.";
    practicePhase.textContent = currentApproval ? "Approval ready" : "Waiting for approval";
    if (!active) return;
    try {
      const value = await request(
        `/api/sources/${active.source.id}/practice-sessions`
      );
      practiceSessions = value.practice_sessions;
      if (!practiceSessions.length) return;
      renderPractice(practiceSessions.at(-1), {restore: true});
    } catch (error) {
      practiceStatus.textContent = error.message;
      practiceStatus.dataset.kind = "error";
    }
  };

  const createPractice = async () => {
    if (!currentApproval || currentApproval.status !== "active") return;
    practiceCreateButton.disabled = true;
    practiceStatus.textContent =
      "Pinning the exact approval, measure map, source, and integer timebase…";
    try {
      const value = await request(
        `/api/approvals/${currentApproval.approval_id}/practice-sessions`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": `practice-create-${crypto.randomUUID()}`,
          },
          body: "",
        }
      );
      const existing = practiceSessions.findIndex(
        (item) => item.practice_session.practice_session_id
          === value.practice.practice_session.practice_session_id
      );
      if (existing >= 0) practiceSessions[existing] = value.practice;
      else practiceSessions.push(value.practice);
      renderPractice(value.practice, {restore: true});
      await loadPracticeUsage({announceFailure: false});
      practiceStatus.textContent =
        "Private practice is ready. Restored paused; choose Start practice.";
      practiceStatus.dataset.kind = "success";
    } catch (error) {
      practiceCreateButton.disabled = false;
      practiceStatus.textContent = error.message;
      practiceStatus.dataset.kind = "error";
      practiceStatus.focus();
    }
  };

  const renderPractice = (value, {restore = false} = {}) => {
    currentPractice = value;
    const session = value.practice_session;
    const attempt = value.attempt;
    const ready = value.availability.status === "ready";
    practiceControls.disabled = !ready;
    practiceCreateButton.disabled = true;
    practicePhase.textContent = ready ? "Ready · paused" : value.availability.status;
    practiceTarget.replaceChildren();
    for (const target of session.targets) {
      const option = document.createElement("option");
      option.value = target.target_id;
      if (target.kind === "section") {
        option.textContent =
          `${target.label} · occurrence ${target.occurrence} · ` +
          `measures ${target.start_measure}–${target.end_measure}`;
      } else {
        option.textContent = target.label;
      }
      practiceTarget.append(option);
    }
    const custom = document.createElement("option");
    custom.value = "custom";
    custom.textContent = "Custom approved frame range";
    practiceTarget.append(custom);
    practiceTarget.value = attempt.target_id || "custom";
    practiceCustomRange.hidden = attempt.selection_kind !== "custom";
    practiceCustomStart.min = String(session.analyzed_range.start_frame);
    practiceCustomStart.max = String(session.analyzed_range.end_frame - 1);
    practiceCustomEnd.min = String(session.analyzed_range.start_frame + 1);
    practiceCustomEnd.max = String(session.analyzed_range.end_frame);
    practiceCustomStart.value = String(attempt.range.start_frame);
    practiceCustomEnd.value = String(attempt.range.end_frame);
    practiceSpeed.value = String(attempt.rate_milli);
    practiceCountIn.value = attempt.count_in.mode;
    practiceLoop.checked = attempt.loop_enabled;
    practiceSourceState.textContent =
      `${value.source_authorization.message} ${value.privacy.excluded_from_songchart_and_exports
        ? "Practice state is excluded from SongChart and exports."
        : ""}`;
    practiceStatus.textContent = ready
      ? (
        restore
          ? "Saved setup restored paused. Playback requires your Start practice action."
          : "Practice setup saved privately."
      )
      : value.availability.message;
    practiceStatus.dataset.kind = ready ? "success" : "error";
    if (restore) {
      pause();
      applyPracticeAttempt(attempt);
    }
  };

  const selectedPracticeRange = () => {
    if (!currentPractice) throw new Error("Prepare or restore approved practice first.");
    if (practiceTarget.value === "custom") {
      const start = Number(practiceCustomStart.value);
      const end = Number(practiceCustomEnd.value);
      if (!Number.isInteger(start) || !Number.isInteger(end) || end <= start) {
        throw new Error("Custom practice end must be greater than its integer start frame.");
      }
      const approved = currentPractice.practice_session.analyzed_range;
      if (start < approved.start_frame || end > approved.end_frame) {
        throw new Error("Custom practice must stay inside the approved chart range.");
      }
      return {targetId: null, start, end};
    }
    const target = currentPractice.practice_session.targets.find(
      (item) => item.target_id === practiceTarget.value
    );
    if (!target) throw new Error("Choose an approved practice range.");
    return {
      targetId: target.target_id,
      start: target.range.start_frame,
      end: target.range.end_frame,
    };
  };

  const practicePayload = ({startAtRange = false} = {}) => {
    const range = selectedPracticeRange();
    const current = Math.round(audio.currentTime * sampleRate);
    const positionFrame = startAtRange || current < range.start || current >= range.end
      ? range.start
      : current;
    return {
      target_id: range.targetId,
      custom_range: range.targetId
        ? null
        : {start_frame: range.start, end_frame: range.end},
      position_frame: positionFrame,
      loop_enabled: practiceLoop.checked,
      rate_milli: Number(practiceSpeed.value),
      count_in: practiceCountIn.value,
    };
  };

  const savePractice = async ({announce = true, startAtRange = false} = {}) => {
    if (!currentPractice || currentPractice.availability.status !== "ready") {
      throw new Error("Approved practice is not available.");
    }
    const payload = practicePayload({startAtRange});
    const sessionId = currentPractice.practice_session.practice_session_id;
    const value = await request(
      `/api/practice-sessions/${sessionId}/attempts`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${currentPractice.head.token}"`,
          "Idempotency-Key": `practice-save-${crypto.randomUUID()}`,
        },
        body: JSON.stringify(payload),
      }
    );
    currentPractice = value.practice;
    const index = practiceSessions.findIndex(
      (item) => item.practice_session.practice_session_id === sessionId
    );
    if (index >= 0) practiceSessions[index] = value.practice;
    applyPracticeAttempt(value.practice.attempt);
    if (announce) {
      practiceStatus.textContent =
        "Setup and safe position saved as an immutable private attempt.";
      practiceStatus.dataset.kind = "success";
    }
    await loadPracticeUsage({announceFailure: false});
    return value.practice;
  };

  const applyPracticeAttempt = (attempt) => {
    audio.playbackRate = attempt.rate_milli / 1000;
    if ("preservesPitch" in audio) audio.preservesPitch = false;
    practiceRange = {
      start: attempt.range.start_frame,
      end: attempt.range.end_frame,
    };
    if (attempt.loop_enabled) {
      setLoop(attempt.range.start_frame, attempt.range.end_frame);
    } else {
      clearLoop();
      loopStart.value = String(attempt.range.start_frame);
      loopEnd.value = String(attempt.range.end_frame);
    }
    seekFrame(attempt.position_frame);
  };

  const selectPracticeTarget = () => {
    if (!currentPractice) return;
    pause();
    cancelPracticeCountIn();
    practiceCustomRange.hidden = practiceTarget.value !== "custom";
    try {
      const range = selectedPracticeRange();
      loopStart.value = String(range.start);
      loopEnd.value = String(range.end);
      practiceRange = {start: range.start, end: range.end};
      seekFrame(range.start);
      if (practiceLoop.checked) setLoop(range.start, range.end);
      else clearLoop();
      practiceStatus.textContent =
        `Selected half-open range ${range.start.toLocaleString()}–${range.end.toLocaleString()}. ` +
        "Save or start to persist it.";
    } catch (error) {
      practiceStatus.textContent = error.message;
      practiceStatus.dataset.kind = "error";
    }
  };

  const startPractice = async () => {
    if (practiceStartPending) return;
    if (!practiceSaveAllowed) {
      cancelPracticeCountIn();
      pause();
      practicePhase.textContent = "Paused";
      announcePracticeSaveLimit();
      return;
    }
    cancelPracticeCountIn();
    const epoch = practiceCountInEpoch;
    practiceStartPending = true;
    practiceStartButton.disabled = true;
    try {
      const value = await savePractice({announce: false});
      const attempt = value.attempt;
      seekFrame(attempt.position_frame);
      if (attempt.count_in.beats > 0) {
        await ensurePracticeAudioContext();
        for (let beat = 1; beat <= attempt.count_in.beats; beat += 1) {
          if (epoch !== practiceCountInEpoch) return;
          practiceStatus.textContent =
            `Count-in ${beat} of ${attempt.count_in.beats} · approved-grid reference`;
          playPracticeClick(beat === 1);
          await delay(attempt.count_in.beat_duration_ms);
        }
      }
      if (epoch !== practiceCountInEpoch) return;
      practiceStatus.textContent =
        "Practicing the saved frame range. Browser loop timing is interaction-grade.";
      practicePhase.textContent = "Playing";
      await play();
      if (audio.paused) {
        practicePhase.textContent = "Paused";
        practiceStatus.textContent =
          "The browser blocked delayed playback after count-in. Press Start practice again.";
        practiceStatus.dataset.kind = "error";
      } else if (!practiceSaveAllowed) {
        practiceStatus.textContent =
          "Practice started from the final available saved action. Further Start, " +
          "Save, Pause and save, and Reset actions are unavailable until project " +
          "practice history is cleared. Main Play and Pause remain available.";
        practiceStatus.dataset.kind = "warning";
      }
    } catch (error) {
      practiceStatus.textContent = error.message;
      practiceStatus.dataset.kind = "error";
      practiceStatus.focus();
    } finally {
      practiceStartPending = false;
      practiceStartButton.disabled =
        currentPractice?.availability.status !== "ready"
        || !practiceSaveAllowed;
    }
  };

  const pausePractice = async () => {
    cancelPracticeCountIn();
    pause();
    practicePhase.textContent = "Paused";
    try {
      await savePractice({announce: true});
    } catch (error) {
      practiceStatus.textContent = error.message;
      practiceStatus.dataset.kind = "error";
    }
  };

  const resetPractice = async () => {
    if (!currentPractice) return;
    cancelPracticeCountIn();
    pause();
    const full = currentPractice.practice_session.targets.find(
      (item) => item.kind === "full"
    );
    practiceTarget.value = full.target_id;
    practiceCustomRange.hidden = true;
    practiceSpeed.value = "1000";
    practiceCountIn.value = "off";
    practiceLoop.checked = false;
    seekFrame(full.range.start_frame);
    clearLoop();
    try {
      await savePractice({announce: true, startAtRange: true});
      practiceStatus.textContent = "Practice setup reset and saved paused.";
    } catch (error) {
      practiceStatus.textContent = error.message;
      practiceStatus.dataset.kind = "error";
    }
  };

  const invalidatePractice = (message) => {
    if (!currentPractice) return;
    cancelPracticeCountIn();
    pause();
    practiceControls.disabled = true;
    practicePhase.textContent = "Refresh required";
    practiceStatus.textContent = message;
    practiceStatus.dataset.kind = "error";
  };

  const cancelPracticeCountIn = () => {
    practiceCountInEpoch += 1;
  };

  const ensurePracticeAudioContext = async () => {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return;
    practiceAudioContext ||= new Context();
    if (practiceAudioContext.state === "suspended") {
      await practiceAudioContext.resume();
    }
  };

  const playPracticeClick = (accent) => {
    if (!practiceAudioContext) return;
    const oscillator = practiceAudioContext.createOscillator();
    const gain = practiceAudioContext.createGain();
    oscillator.frequency.value = accent ? 880 : 660;
    gain.gain.setValueAtTime(0.0001, practiceAudioContext.currentTime);
    gain.gain.exponentialRampToValueAtTime(
      0.12,
      practiceAudioContext.currentTime + 0.003
    );
    gain.gain.exponentialRampToValueAtTime(
      0.0001,
      practiceAudioContext.currentTime + 0.045
    );
    oscillator.connect(gain);
    gain.connect(practiceAudioContext.destination);
    oscillator.start();
    oscillator.stop(practiceAudioContext.currentTime + 0.05);
  };

  const delay = (milliseconds) => new Promise(
    (resolve) => window.setTimeout(resolve, milliseconds)
  );

  const renderSections = (markers, {disabled = false} = {}) => {
    sectionList.replaceChildren();
    for (const marker of markers) {
      const item = document.createElement("li");
      const seekButton = document.createElement("button");
      seekButton.type = "button";
      seekButton.className = "section-seek";
      seekButton.textContent = `${marker.label} · frame ${marker.frame.toLocaleString()}`;
      seekButton.addEventListener("click", () => seekFrame(marker.frame));
      const labelInput = document.createElement("input");
      labelInput.type = "text";
      labelInput.maxLength = 80;
      labelInput.value = marker.label;
      labelInput.setAttribute("aria-label", `Rename ${marker.label}`);
      const rename = document.createElement("button");
      rename.type = "button";
      rename.textContent = "Rename";
      rename.addEventListener("click", () =>
        applyReviewEdit("rename_section", {marker_id: marker.id, label: labelInput.value})
      );
      const frameInput = document.createElement("input");
      frameInput.type = "number";
      frameInput.min = String(currentReview.timeline.analyzed_range.start_frame);
      frameInput.max = String(currentReview.timeline.analyzed_range.end_frame - 1);
      frameInput.value = String(marker.frame);
      frameInput.setAttribute("aria-label", `Move ${marker.label} to frame`);
      const move = document.createElement("button");
      move.type = "button";
      move.textContent = "Move";
      move.addEventListener("click", () =>
        applyReviewEdit("move_section", {marker_id: marker.id, frame: Number(frameInput.value)})
      );
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "quiet";
      remove.textContent = "Remove";
      remove.addEventListener("click", () =>
        applyReviewEdit("remove_section", {marker_id: marker.id})
      );
      item.append(seekButton, labelInput, rename, frameInput, move, remove);
      Array.from(item.querySelectorAll("button, input")).forEach((control, index) => {
        control.dataset.focusKey = `section:${marker.id}:${index}`;
        if (index > 0) control.disabled = disabled;
      });
      sectionList.append(item);
    }
  };

  const renderReviewSegments = (segments, {disabled = false} = {}) => {
    reviewLane.replaceChildren();
    segments.forEach((segment, index) => {
      const item = document.createElement("li");
      item.className = "review-card";
      item.dataset.segmentId = segment.id;
      item.dataset.startFrame = String(segment.range.start_frame);
      item.dataset.endFrame = String(segment.range.end_frame);
      if (segment.id === selectedReviewSegmentId) item.classList.add("selected");

      const heading = document.createElement("button");
      heading.type = "button";
      heading.className = "review-seek";
      heading.textContent =
        `${segment.label || (segment.state === "no_chord" ? "N.C." : "Unknown")} · ` +
        `${segment.range.start_frame.toLocaleString()}–${segment.range.end_frame.toLocaleString()} · ` +
        `${segment.label_status}`;
      heading.addEventListener("click", () => {
        selectedReviewSegmentId = segment.id;
        seekFrame(segment.range.start_frame);
        for (const card of reviewLane.querySelectorAll(".review-card")) {
          card.classList.toggle("selected", card.dataset.segmentId === segment.id);
        }
      });

      const controls = document.createElement("div");
      controls.className = "segment-actions";
      const accept = actionButton("Accept current", () =>
        applyReviewEdit("accept_current", {segment_id: segment.id})
      );
      accept.disabled = segment.state === "unknown";
      controls.append(accept);

      const raw = rawSegmentFor(segment);
      if (raw?.candidates?.length) {
        const candidate = document.createElement("select");
        candidate.setAttribute("aria-label", "Choose machine candidate");
        for (const optionValue of raw.candidates) {
          const option = document.createElement("option");
          option.value = String(optionValue.rank);
          option.textContent =
            `${optionValue.raw_label} · ${formatConfidence(optionValue.confidence_ppm)} · raw proposal`;
          option.selected = optionValue.rank === segment.selected_candidate_rank;
          candidate.append(option);
        }
        const choose = actionButton("Choose candidate", () =>
          applyReviewEdit("select_candidate", {
            segment_id: segment.id,
            candidate_rank: Number(candidate.value),
          })
        );
        controls.append(candidate, choose);
      }

      const label = document.createElement("input");
      label.type = "text";
      label.maxLength = 80;
      label.value = segment.label || "";
      label.placeholder = "Chord label";
      label.setAttribute("aria-label", "Manual chord label");
      controls.append(
        label,
        actionButton("Rename", () =>
          applyReviewEdit("set_label", {segment_id: segment.id, label: label.value})
        ),
        actionButton("Mark N.C.", () =>
          applyReviewEdit("set_no_chord", {segment_id: segment.id})
        ),
        actionButton("Mark Unknown", () =>
          applyReviewEdit("set_unknown", {segment_id: segment.id})
        ),
        actionButton("Split at position", () =>
          applyReviewEdit("split", {
            segment_id: segment.id,
            frame: Number(seek.value),
          })
        )
      );

      if (index > 0) {
        controls.append(actionButton("Merge previous", () =>
          applyReviewEdit("merge", {
            left_segment_id: segments[index - 1].id,
            right_segment_id: segment.id,
          })
        ));
        const boundary = document.createElement("input");
        boundary.type = "number";
        boundary.value = String(segment.range.start_frame);
        boundary.setAttribute("aria-label", "Shared boundary frame");
        controls.append(
          boundary,
          actionButton("Move shared boundary", () =>
            applyReviewEdit("move_boundary", {
              left_segment_id: segments[index - 1].id,
              right_segment_id: segment.id,
              frame: Number(boundary.value),
            })
          )
        );
      }
      if (index > 0 && index < segments.length - 1) {
        const start = document.createElement("input");
        start.type = "number";
        start.value = String(segment.range.start_frame);
        start.setAttribute("aria-label", "New segment start frame");
        controls.append(
          start,
          actionButton("Move; adjust adjacent", () =>
            applyReviewEdit("move_segment", {
              segment_id: segment.id,
              start_frame: Number(start.value),
              resolution: "adjust_adjacent",
            })
          )
        );
      }
      item.append(heading, controls);
      Array.from(controls.querySelectorAll("button, input, select")).forEach((control, actionIndex) => {
        control.dataset.focusKey = `segment:${segment.id}:${actionIndex}`;
        control.disabled = control.disabled || disabled;
      });
      reviewLane.append(item);
    });
    highlightReview(Math.round(audio.currentTime * sampleRate));
  };

  const actionButton = (label, handler) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", handler);
    return button;
  };

  const restoreReviewFocus = (focusKey) => {
    const target = focusKey
      ? Array.from(document.querySelectorAll("[data-focus-key]"))
        .find((item) => item.dataset.focusKey === focusKey && !item.disabled)
      : null;
    (target || reviewStatus).focus();
  };

  const rawSegmentFor = (segment) => {
    if (!currentTimeline || segment.origin_ordinals.length !== 1) return null;
    return currentTimeline.segments.find(
      (item) => item.ordinal === segment.origin_ordinals[0]
    ) || null;
  };

  const highlightReview = (frame) => {
    for (const item of reviewLane.querySelectorAll(".review-card")) {
      const activeSegment =
        Number(item.dataset.startFrame) <= frame && frame < Number(item.dataset.endFrame);
      if (activeSegment) {
        item.setAttribute("aria-current", "true");
      } else {
        item.removeAttribute("aria-current");
      }
    }
  };

  const formatConfidence = (value) => `${(value / 10000).toFixed(1)}% confidence`;

  const formatTime = (seconds) => {
    const minutes = Math.floor(seconds / 60);
    const remaining = seconds - minutes * 60;
    return `${String(minutes).padStart(2, "0")}:${remaining.toFixed(3).padStart(6, "0")}`;
  };

  fileInput.addEventListener("change", resetFileAuthorization);
  authorization.addEventListener("change", updateImportState);
  importButton.addEventListener("click", importAudio);
  remoteName.addEventListener("input", updateAcquisitionInput);
  remoteUrl.addEventListener("input", resetRemoteAuthorization);
  remoteAuthorization.addEventListener("change", updateAcquisitionInput);
  toggleRemoteUrl.addEventListener("click", () => {
    const visible = remoteUrl.type === "text";
    remoteUrl.type = visible ? "password" : "text";
    toggleRemoteUrl.textContent = visible ? "Show URL" : "Hide URL";
    toggleRemoteUrl.setAttribute("aria-pressed", String(!visible));
    remoteUrl.focus();
  });
  acquireButton.addEventListener("click", startAcquisition);
  cancelAcquisitionButton.addEventListener("click", cancelAcquisition);
  retryAcquisitionButton.addEventListener("click", retryAcquisition);
  forgetAcquisitionButton.addEventListener("click", forgetAcquisitionLocator);
  sourceSelect.addEventListener("change", () => loadSource(sourceSelect.value));
  playButton.addEventListener("click", play);
  pauseButton.addEventListener("click", pause);
  seek.addEventListener("input", () => seekFrame(Number(seek.value)));
  setLoopButton.addEventListener("click", () => setLoop(Number(loopStart.value), Number(loopEnd.value)));
  clearLoopButton.addEventListener("click", clearLoop);
  analyzeButton.addEventListener("click", startAnalysis);
  cancelAnalysisButton.addEventListener("click", cancelAnalysis);
  retryAnalysisButton.addEventListener("click", retryAnalysis);
  analysisRunSelect.addEventListener("change", () => selectAnalysisRun(analysisRunSelect.value));
  startReviewButton.addEventListener("click", startReview);
  reviewSessionSelect.addEventListener("change", () => loadReview(reviewSessionSelect.value));
  undoReviewButton.addEventListener("click", () => reviewMutation("undo", {}));
  redoReviewButton.addEventListener("click", () => {
    if (!currentReview?.head.redo_revision_id) return;
    reviewMutation("redo", {revision_id: currentReview.head.redo_revision_id});
  });
  resetReviewButton.addEventListener("click", () => {
    if (!window.confirm(
      "Reset the editable timeline and sections to the raw machine draft? " +
      "The current revision remains recoverable through undo."
    )) return;
    applyReviewEdit("reset_to_raw", {});
  });
  acceptBoundariesButton.addEventListener("click", () =>
    applyReviewEdit("accept_boundaries", {})
  );
  finishReviewButton.addEventListener("click", () => applyReviewEdit("finish_review", {}));
  retryReviewEditButton.addEventListener("click", () => {
    if (!pendingConflict) return;
    const conflict = pendingConflict;
    selectedReviewSegmentId = conflict.selected;
    reviewMutation(conflict.suffix, conflict.body, {
      idempotencyKey: conflict.idempotencyKey,
    });
  });
  discardReviewEditButton.addEventListener("click", () => {
    pendingConflict = null;
    reviewConflictActions.hidden = true;
    setReviewStatus("Rejected edit discarded. The latest saved revision is unchanged.");
    reviewStatus.focus();
  });
  insertNoChordButton.addEventListener("click", () =>
    applyReviewEdit("insert_no_chord", {
      start_frame: Number(noChordStart.value),
      end_frame: Number(noChordEnd.value),
    })
  );
  addSectionButton.addEventListener("click", () =>
    applyReviewEdit("add_section", {
      label: sectionLabel.value,
      frame: Number(sectionFrame.value),
    })
  );
  previewPromotionButton.addEventListener("click", previewPromotion);
  addPlayheadBoundaryButton.addEventListener("click", addPlayheadBoundary);
  promotionControls.addEventListener("input", invalidatePromotionPreview);
  promotionPreviewFormat.addEventListener("change", renderPromotionPreview);
  approvePromotionButton.addEventListener("click", approvePromotion);
  promotionExportFormat.addEventListener("change", renderPromotionExport);
  revokePromotionButton.addEventListener("click", revokePromotion);
  practiceCreateButton.addEventListener("click", createPractice);
  practiceTarget.addEventListener("change", selectPracticeTarget);
  practiceCustomStart.addEventListener("change", selectPracticeTarget);
  practiceCustomEnd.addEventListener("change", selectPracticeTarget);
  practiceLoop.addEventListener("change", selectPracticeTarget);
  practiceSaveButton.addEventListener("click", () => {
    savePractice().catch((error) => {
      practiceStatus.textContent = error.message;
      practiceStatus.dataset.kind = "error";
    });
  });
  practiceStartButton.addEventListener("click", startPractice);
  practicePauseButton.addEventListener("click", pausePractice);
  practiceClearButton.addEventListener("click", resetPractice);
  practiceHistoryClearButton.addEventListener("click", openPracticeHistoryDialog);
  practiceHistoryConfirmation.addEventListener("input", () => {
    practiceHistorySubmitButton.disabled =
      practiceHistoryResetPending
      || practiceHistoryConfirmation.value !== "CLEAR ALL PRACTICE HISTORY";
  });
  practiceHistoryCancelButton.addEventListener("click", closePracticeHistoryDialog);
  practiceHistoryForm.addEventListener("submit", clearPracticeHistory);
  practiceHistoryDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closePracticeHistoryDialog();
  });
  audio.addEventListener("timeupdate", () => updatePosition(enforceLoopBoundary()));
  audio.addEventListener("ended", restartLoopAtMediaEnd);
  window.addEventListener("resize", resizeAndDraw);

  canvas.addEventListener("pointerdown", (event) => {
    dragStartFrame = frameFromPointer(event);
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (dragStartFrame === null) return;
    const current = frameFromPointer(event);
    loopStart.value = String(Math.min(dragStartFrame, current));
    loopEnd.value = String(Math.max(dragStartFrame, current));
  });
  canvas.addEventListener("pointerup", (event) => {
    const current = frameFromPointer(event);
    if (dragStartFrame === current) {
      seekFrame(current);
    } else {
      setLoop(Math.min(dragStartFrame, current), Math.max(dragStartFrame, current));
    }
    dragStartFrame = null;
  });
  document.addEventListener("keydown", (event) => {
    if (practiceHistoryDialog.open) return;
    if (event.key.toLowerCase() === "l" && !event.target.matches("input, select, button")) {
      loopRange ? clearLoop() : setLoop(Number(loopStart.value), Number(loopEnd.value));
    }
    if (event.key === "Escape" && currentPractice) {
      cancelPracticeCountIn();
      pause();
      practicePhase.textContent = "Paused";
      practiceStatus.textContent = "Practice playback and count-in paused.";
    }
    if (
      event.key === " "
      && currentPractice
      && !event.target.matches("input, select, button, textarea")
    ) {
      event.preventDefault();
      if (audio.paused) startPractice();
      else if (practiceSaveAllowed) pausePractice();
      else {
        cancelPracticeCountIn();
        pause();
        practicePhase.textContent = "Paused";
        announcePracticeSaveLimit({pausedWithoutSaving: true});
      }
    }
    if (
      event.key.toLowerCase() === "p"
      && currentPractice
      && !event.target.matches("input, select, button, textarea")
    ) {
      practiceTarget.focus();
    }
  });
  shutdownButton.addEventListener("click", async () => {
    if (!window.confirm("Stop the local ChordAtlas Studio service? Imported media stays in this project.")) {
      return;
    }
    try {
      await request("/api/shutdown", {method: "POST"});
      setStatus("Studio stopped. You can close this tab.", "success");
      document.querySelectorAll("button, input, select").forEach((control) => {
        control.disabled = true;
      });
    } catch (error) {
      setStatus(error.message, "error");
    }
  });

  bootstrap();
})();
